"""Run probes from a job list on a schedule, store results, exit cleanly."""

from __future__ import annotations

import heapq
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from lacuna.config import Config, Job
from lacuna.db import Database, LossSample, TputSample
from lacuna.probes import split_host_port
from lacuna.probes.icmp import run_ping
from lacuna.probes.tcp import run_tcp
from lacuna.probes.throughput import run_http_throughput, run_tcp_throughput
from lacuna.probes.udp import run_udp


@dataclass(slots=True)
class Outcome:
    sample: LossSample | TputSample
    params: dict[str, Any] = field(default_factory=dict)


def run_job(job: Job, stop: threading.Event | None = None) -> Outcome:
    """Execute one probe job with its params and return the sample."""
    p = job.params
    if job.kind == "icmp":
        count, interval = int(p.get("count", 100)), float(p.get("interval", 0.2))
        return Outcome(run_ping(job.target, count, interval), {"count": count, "interval": interval})
    if job.kind == "udp":
        host, port = split_host_port(job.target)
        args = {"count": int(p.get("count", 500)), "pps": float(p.get("pps", 20)),
                "size": int(p.get("size", 200))}
        return Outcome(run_udp(host, port, **args), args)
    if job.kind == "tcp":
        host, port = split_host_port(job.target)
        args = {"count": int(p.get("count", 50)), "interval": float(p.get("interval", 0.5))}
        return Outcome(run_tcp(host, port, stop=stop, **args), args)
    secs, streams = float(p.get("secs", 10)), int(p.get("streams", 1))
    if job.kind == "http":
        sample, per = run_http_throughput(job.target, secs, streams)
    else:
        host, port = split_host_port(job.target)
        sample, per = run_tcp_throughput(host, port, secs, streams, str(p.get("direction", "down")))
    return Outcome(sample, {"secs": secs, "streams": streams, "per_stream_mbps": per})


def store(db: Database, profile: str, job: Job, out: Outcome, ts: float | None = None) -> int:
    run_id = db.add_run(profile, job.kind, job.target, out.params, ts=ts)
    if isinstance(out.sample, LossSample):
        db.add_loss(run_id, out.sample)
    else:
        db.add_tput(run_id, out.sample)
    return run_id


def summary_line(job: Job, sample: LossSample | TputSample) -> str:
    if isinstance(sample, TputSample):
        return f"{job.label:<44} {sample.direction} {sample.mbps:8.2f} Mbit/s  {sample.streams} stream(s)"
    rtt = f"{sample.rtt_p50:.1f}" if sample.rtt_p50 is not None else "n/a"
    jit = f"{sample.jitter_ms:.2f}" if sample.jitter_ms is not None else "n/a"
    return (f"{job.label:<44} loss {sample.loss_pct:5.1f}% ({sample.sent - sample.recv}/{sample.sent})"
            f"  rtt p50 {rtt} ms  jitter {jit} ms")


class Monitor:
    def __init__(
        self, cfg: Config, db: Database, stop: threading.Event | None = None,
        log: Callable[[str], None] | None = None,
    ):
        self.cfg = cfg
        self.db = db
        self.stop = stop or threading.Event()
        self.log = log or (lambda msg: print(msg, flush=True))
        self.runs = 0
        self.failures = 0

    def run(self, max_runs: int | None = None) -> None:
        """Loop until stop is set (or max_runs probes ran, for tests)."""
        now = time.monotonic()
        # stagger first runs so probes do not pile up at t=0
        heap = [(now + 1.0 * i, i, job) for i, job in enumerate(self.cfg.jobs)]
        heapq.heapify(heap)
        while heap and not self.stop.is_set():
            due, idx, job = heapq.heappop(heap)
            if self.stop.wait(max(0.0, due - time.monotonic())):
                break
            self._run_one(job)
            if max_runs is not None and self.runs + self.failures >= max_runs:
                break
            nxt = max(due + job.every_s, time.monotonic() + 1.0)
            heapq.heappush(heap, (nxt, idx, job))

    def _run_one(self, job: Job) -> None:
        stamp = time.strftime("%H:%M:%S")
        try:
            out = run_job(job, self.stop)
            store(self.db, self.cfg.profile, job, out)
            self.runs += 1
            self.log(f"{stamp} {summary_line(job, out.sample)}")
        except Exception as exc:  # a failing target must not stop the monitor
            self.failures += 1
            self.log(f"{stamp} {job.label:<44} FAILED: {exc}")


def install_signal_handlers(stop: threading.Event) -> None:
    def handler(signum, _frame):
        print("\nstopping...", file=sys.stderr, flush=True)
        stop.set()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
