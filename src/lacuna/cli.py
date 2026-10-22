"""Command line interface."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
from dataclasses import asdict
from pathlib import Path

from lacuna import __version__, export
from lacuna.config import (
    DEFAULT_PORT,
    RATE_CAPS,
    ConfigError,
    Job,
    check_rate,
    default_config_path,
    default_db_path,
    default_profile,
    load_config,
)
from lacuna.db import Database, LossSample, TputSample, parse_since
from lacuna.monitor import Monitor, Outcome, install_signal_handlers, run_job, store
from lacuna.probes import split_host_port
from lacuna.probes.icmp import PingError
from lacuna.probes.throughput import ThroughputError
from lacuna.probes.udp import UdpProbeError
from lacuna.report.html import build_report, profile_summary

RUNTIME_ERRORS = (OSError, PingError, UdpProbeError, ThroughputError)


# ---- output helpers ----

def kv_table(rows: list[tuple[str, str]]) -> str:
    w = max(len(k) for k, _ in rows)
    return "\n".join(f"{k:<{w}}  {v}" for k, v in rows)


def _f(v: float | None, spec: str = ".2f") -> str:
    return "n/a" if v is None else format(v, spec)


def format_loss(job: Job, profile: str, s: LossSample) -> str:
    rows = [
        ("target", f"{job.target} ({job.kind})"),
        ("profile", profile),
        ("packets", f"{s.sent} sent, {s.recv} received"),
        ("loss", f"{s.loss_pct:.2f} %"),
    ]
    if s.up_loss_pct is not None:
        rows.append(("direction", f"uplink {s.up_loss_pct:.2f} %, downlink {_f(s.down_loss_pct)} %"))
    rows.append(("rtt ms", "  ".join(f"{k} {_f(getattr(s, 'rtt_' + k), '.2f')}"
                                     for k in ("min", "mean", "p50", "p95", "p99", "max"))))
    rows.append(("jitter", f"{_f(s.jitter_ms)} ms"))
    extra = f"longest burst {s.max_burst}"
    if s.dup is not None:
        extra += f", duplicates {s.dup}"
    if s.reordered is not None:
        extra += f", reordered {s.reordered}"
    rows.append(("bursts", extra))
    if s.ge_p is not None and s.ge_r:
        rows.append(("gilbert", f"p {s.ge_p:.4f}  r {s.ge_r:.3f}  mean burst {1 / s.ge_r:.1f}"
                     + ("  (small sample, treat as rough)" if s.sent < 200 else "")))
    return kv_table(rows)


def format_tput(job: Job, profile: str, s: TputSample, per: list[float]) -> str:
    rows = [
        ("target", f"{job.target} ({job.kind})"),
        ("profile", profile),
        ("direction", f"{s.direction}, {s.streams} stream(s), {s.secs:.1f} s"),
        ("transferred", f"{s.bytes / 1e6:.2f} MB"),
        ("throughput", f"{s.mbps:.2f} Mbit/s total"),
    ]
    if len(per) > 1:
        rows.append(("per stream", "  ".join(f"{m:.2f}" for m in per) + " Mbit/s"))
    if s.cwnd is None:
        rows.append(("tcp_info", "not available on this OS"))
    else:
        retrans = "n/a (receiver side)" if s.retrans is None else str(s.retrans)
        rows.append(("tcp_info", f"retrans {retrans}  cwnd {s.cwnd}  rtt {_f(s.rtt_ms, '.1f')} ms  "
                                 f"ssthresh {s.ssthresh}"))
    return kv_table(rows)


# ---- probe commands ----

def _warn(msg: str | None) -> None:
    if msg:
        print(msg, file=sys.stderr)


def _finish_probe(args: argparse.Namespace, job: Job) -> int:
    profile = args.profile or default_profile()
    out = run_job(job)
    if not args.no_store:
        with Database(args.db or default_db_path()) as db:
            store(db, profile, job, out)
    _print_outcome(args, job, profile, out)
    return 0


def _print_outcome(args: argparse.Namespace, job: Job, profile: str, out: Outcome) -> None:
    if args.json:
        doc = {"kind": job.kind, "target": job.target, "profile": profile,
               "sample": asdict(out.sample), "params": out.params}
        print(json.dumps(doc, indent=2))
    elif isinstance(out.sample, LossSample):
        print(format_loss(job, profile, out.sample))
    else:
        print(format_tput(job, profile, out.sample, out.params.get("per_stream_mbps", [])))


def cmd_ping(args: argparse.Namespace) -> int:
    _warn(check_rate("icmp", 1.0 / args.interval, args.unsafe_rate))
    _positive(count=args.count, interval=args.interval)
    return _finish_probe(args, Job("icmp", args.target, 0, {"count": args.count, "interval": args.interval}))


def cmd_udp(args: argparse.Namespace) -> int:
    _warn(check_rate("udp", args.pps, args.unsafe_rate))
    split_host_port(args.target)
    _positive(count=args.count, pps=args.pps)
    if not 32 <= args.size <= 1400:
        raise ValueError("--size must be between 32 and 1400")
    params = {"count": args.count, "pps": args.pps, "size": args.size}
    return _finish_probe(args, Job("udp", args.target, 0, params))


def cmd_tcp(args: argparse.Namespace) -> int:
    _warn(check_rate("tcp", 1.0 / args.interval, args.unsafe_rate))
    split_host_port(args.target)
    _positive(count=args.count, interval=args.interval)
    return _finish_probe(args, Job("tcp", args.target, 0, {"count": args.count, "interval": args.interval}))


def cmd_throughput(args: argparse.Namespace) -> int:
    if bool(args.target) == bool(args.url):
        raise ValueError("give either HOST:PORT or --url")
    _warn(check_rate("throughput", args.secs, args.unsafe_rate))
    kind = "http" if args.url else "throughput"
    streams = args.streams or (4 if args.url else 1)
    params = {"secs": args.secs, "streams": streams, "direction": args.direction}
    if args.target:
        split_host_port(args.target)
    return _finish_probe(args, Job(kind, args.url or args.target, 0, params))


def _positive(**vals: float) -> None:
    for name, v in vals.items():
        if v <= 0:
            raise ValueError(f"--{name} must be positive")


# ---- server, monitor, tui ----

def cmd_server(args: argparse.Namespace) -> int:
    from lacuna.server import LacunaServer

    srv = LacunaServer(args.bind, args.port)
    srv.start()
    print(f"lacuna server: udp {args.bind}:{args.port}, tcp {args.bind}:{srv.tcp_port}. Ctrl+C to stop.")
    stop = threading.Event()
    install_signal_handlers(stop)
    try:
        stop.wait()
    finally:
        srv.stop()
    return 0


def cmd_monitor(args: argparse.Namespace) -> int:
    path = args.config
    if path is None and default_config_path().exists():
        path = str(default_config_path())
    cfg = load_config(path, args.profile)
    if args.db:
        cfg.db_path = Path(args.db)
    print(f"lacuna monitor: profile {cfg.profile}, {len(cfg.jobs)} jobs, db {cfg.db_path}")
    for j in cfg.jobs:
        print(f"  every {j.every_s:g}s  {j.label}")
    stop = threading.Event()
    install_signal_handlers(stop)
    with Database(cfg.db_path) as db:
        mon = Monitor(cfg, db, stop)
        mon.run()
    print(f"stopped after {mon.runs} runs, {mon.failures} failures")
    return 0


def cmd_tui(args: argparse.Namespace) -> int:
    from lacuna.tui import run_tui

    run_tui(str(args.db or default_db_path()), args.profile)
    return 0


# ---- reporting ----

def _since(args: argparse.Namespace) -> float | None:
    return parse_since(args.since)


def cmd_report(args: argparse.Namespace) -> int:
    with Database(args.db or default_db_path()) as db:
        if not db.counts()["runs"]:
            raise ValueError("the database has no data. Run `lacuna demo` or a probe first")
        html = build_report(db, args.profile or None, _since(args))
    Path(args.output).write_text(html, encoding="utf-8")
    print(Path(args.output).resolve())
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    cols = []
    with Database(args.db or default_db_path()) as db:
        since = _since(args)
        for name in (args.profile_a, args.profile_b):
            loss, tput = db.loss_rows([name], since), db.tput_rows([name], since)
            if not loss and not tput:
                raise ValueError(f"no data for profile {name!r}. Known: {', '.join(db.profiles()) or 'none'}")
            cols.append(profile_summary(name, loss, tput))
    rows = [
        ("packet runs", [str(c.loss_samples) for c in cols]),
        ("packets sent", [str(c.sent) for c in cols]),
        ("mean loss %", [_f(c.loss_pct) for c in cols]),
        ("p95 RTT ms", [_f(c.rtt_p95, ".1f") for c in cols]),
        ("mean jitter ms", [_f(c.jitter_ms) for c in cols]),
        ("median Mbit/s", [_f(c.tput_median, ".1f") for c in cols]),
        ("longest burst", [str(c.longest_burst if c.longest_burst is not None else "n/a") for c in cols]),
        ("GE mean burst", [_f(1 / c.ge.r if c.ge and c.ge.r else None, ".1f") for c in cols]),
    ]
    w = max(len(c.profile) for c in cols) + 2
    print(f"{'':<16}" + "".join(f"{c.profile:>{max(w, 12)}}" for c in cols))
    for label, vals in rows:
        print(f"{label:<16}" + "".join(f"{v:>{max(w, 12)}}" for v in vals))
    for c in cols:
        print(f"{c.profile}: {c.verdict}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    with Database(args.db or default_db_path()) as db:
        fn = export.to_json if args.format == "json" else export.to_csv
        text = fn(db, args.profile or None, _since(args))
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(Path(args.output).resolve(), file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from lacuna.demo import demo_db_path, run_demo

    out = run_demo(Path(args.output), args.base_port)
    print(f"report: {out.resolve()}")
    print(f"data:   {demo_db_path()} (separate from your real database)")
    return 0


# ---- doctor ----

def _check_ping() -> tuple[str, str]:
    exe = shutil.which("ping")
    if not exe:
        return "WARN", "ping not found, ICMP probes disabled. Ubuntu: sudo apt install iputils-ping"
    try:
        proc = subprocess.run([exe, "-c", "1", "127.0.0.1"], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "WARN", f"{exe} failed to run: {exc}"
    if proc.returncode != 0:
        return "WARN", f"{exe} cannot ping 127.0.0.1 as this user (exit {proc.returncode})"
    return "OK", exe


def _check_db(path: Path) -> tuple[str, str]:
    try:
        with Database(path) as db:
            db.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('doctor_ok', '1')")
            db.conn.commit()
            return "OK", f"{path} (schema v{db.schema_version()})"
    except Exception as exc:
        return "FAIL", f"{path}: {exc}"


def _check_loopback() -> tuple[str, str]:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 0))
            s.settimeout(1)
            s.sendto(b"x", s.getsockname())
            s.recvfrom(10)
        return "OK", "udp loopback works"
    except OSError as exc:
        return "FAIL", f"udp loopback: {exc}"


def run_checks(db_path: Path) -> list[tuple[str, str, str]]:
    from lacuna import config

    py_ok = sys.version_info >= (3, 10)
    checks = [
        ("python", "OK" if py_ok else "FAIL", f"{platform.python_version()} ({platform.system()})"),
        ("toml", *(("OK", "tomllib/tomli available") if config.tomllib else ("WARN", "pip install tomli"))),
        ("ping", *_check_ping()),
        ("database", *_check_db(db_path)),
        ("loopback", *_check_loopback()),
        ("tcp_info", *(("OK", "available") if hasattr(socket, "TCP_INFO")
                       else ("WARN", "Linux only. Throughput rows will have empty retrans/cwnd"))),
    ]
    return checks


def cmd_doctor(args: argparse.Namespace) -> int:
    checks = run_checks(Path(args.db or default_db_path()))
    for name, status, detail in checks:
        print(f"[{status:<4}] {name:<9} {detail}")
    failed = [n for n, s, _ in checks if s == "FAIL"]
    print("all required checks passed" if not failed else f"failed: {', '.join(failed)}")
    return 1 if failed else 0


# ---- parser ----

def _db_parent() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--db", metavar="FILE", default=os.environ.get("LACUNA_DB"),
                   help="SQLite file (default: $XDG_DATA_HOME/lacuna/lacuna.db)")
    return p


def _probe_parent() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--profile", metavar="NAME", help="label for this network (default: hostname)")
    p.add_argument("--no-store", action="store_true", help="do not write to the database")
    p.add_argument("--json", action="store_true", help="print machine-readable JSON")
    p.add_argument("--unsafe-rate", action="store_true", help="allow rates above the hard caps")
    return p


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="lacuna", description="Measure real packet loss, jitter, RTT and throughput.")
    ap.add_argument("--version", action="version", version=f"lacuna {__version__}")
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND")
    db, probe = _db_parent(), _probe_parent()
    caps = ", ".join(f"{k} {c.default:g}/{c.hard:g}" for k, c in RATE_CAPS.items())

    def add(name: str, func, help: str, *parents) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, parents=[db, *parents], help=help, description=help)
        sp.set_defaults(func=func)
        return sp

    sp = add("demo", cmd_demo, "offline end to end run with a loss simulator")
    sp.add_argument("-o", "--output", default="lacuna-report.html")
    sp.add_argument("--base-port", type=int, default=DEFAULT_PORT,
                    help="server UDP port. The loss simulator uses base + 10")
    sp = add("server", cmd_server, "run the UDP echo and TCP throughput server")
    sp.add_argument("--bind", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=DEFAULT_PORT, help="UDP port. TCP uses port + 1")
    sp = add("ping", cmd_ping, "ICMP probe through the system ping", probe)
    sp.add_argument("target")
    sp.add_argument("--count", type=int, default=100)
    sp.add_argument("--interval", type=float, default=0.2, help=f"seconds between packets (caps: {caps})")
    sp = add("udp", cmd_udp, "UDP probe against a lacuna server", probe)
    sp.add_argument("target", metavar="HOST:PORT")
    sp.add_argument("--count", type=int, default=500)
    sp.add_argument("--pps", type=float, default=20)
    sp.add_argument("--size", type=int, default=200)
    sp = add("tcp", cmd_tcp, "TCP connect probe", probe)
    sp.add_argument("target", metavar="HOST:PORT")
    sp.add_argument("--count", type=int, default=50)
    sp.add_argument("--interval", type=float, default=0.5)
    sp = add("throughput", cmd_throughput, "TCP or HTTP throughput test", probe)
    sp.add_argument("target", nargs="?", metavar="HOST:PORT", help="lacuna server (UDP port; TCP is port + 1)")
    sp.add_argument("--url", help="HTTP(S) URL to download instead")
    sp.add_argument("--secs", type=float, default=10)
    sp.add_argument("--streams", type=int, default=None, help="parallel streams (default 1, or 4 with --url)")
    sp.add_argument("--direction", choices=("up", "down"), default="down")
    sp = add("monitor", cmd_monitor, "run probes on a schedule until Ctrl+C")
    sp.add_argument("--config", metavar="FILE")
    sp.add_argument("--profile", metavar="NAME")
    add("tui", cmd_tui, "live terminal dashboard from the database").add_argument("--profile", metavar="NAME")
    sp = add("report", cmd_report, "write the HTML report")
    sp.add_argument("--profile", action="append", metavar="NAME", help="repeat to include several")
    sp.add_argument("--since", metavar="AGE", help="for example 90m, 12h, 7d")
    sp.add_argument("-o", "--output", default="lacuna-report.html")
    sp = add("compare", cmd_compare, "compare two profiles in the terminal")
    sp.add_argument("profile_a")
    sp.add_argument("profile_b")
    sp.add_argument("--since", metavar="AGE")
    sp = add("export", cmd_export, "export stored samples")
    sp.add_argument("--format", choices=("csv", "json"), default="csv")
    sp.add_argument("--profile", action="append", metavar="NAME")
    sp.add_argument("--since", metavar="AGE")
    sp.add_argument("-o", "--output", metavar="FILE")
    add("doctor", cmd_doctor, "check python, ping, database path and permissions")
    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return args.func(args) or 0
    except (ConfigError, ValueError) as exc:
        print(f"lacuna: error: {exc}", file=sys.stderr)
        return 2
    except RUNTIME_ERRORS as exc:
        print(f"lacuna: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
