"""Live terminal view that reads the database every couple of seconds."""

from __future__ import annotations

import sys
import time
from collections.abc import Sequence

from lacuna.db import Database

BLOCKS = "▁▂▃▄▅▆▇█"
PACKET_KINDS = ("udp", "icmp")


def sparkline(values: Sequence[float], width: int) -> str:
    vals = list(values)[-width:]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    span = hi - lo
    if span == 0:
        return BLOCKS[0 if hi == 0 else 3] * len(vals)
    return "".join(BLOCKS[min(int((v - lo) / span * 7.999), 7)] for v in vals)


def _age(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def _num(v: float | None, spec: str) -> str:
    return "-" if v is None else format(v, spec)


def build_lines(db: Database, profile: str | None, width: int = 100, now: float | None = None) -> list[str]:
    """Pure function: the screen as a list of text lines."""
    now = time.time() if now is None else now
    if profile is None:
        names = db.profiles()
        if not names:
            return ["No data yet. Run `lacuna monitor` or a probe command first."]
        profile = names[-1] if len(names) == 1 else _latest_profile(db)
    loss = db.loss_rows([profile])
    tput = db.tput_rows([profile])
    lines = [f"Lacuna live view   profile: {profile}   {time.strftime('%H:%M:%S', time.localtime(now))}   q to quit",
             ""]
    if not loss and not tput:
        return lines + ["No samples for this profile yet."]
    hour = [r for r in loss if now - r["ts"] <= 3600 and r["kind"] in PACKET_KINDS]
    sent = sum(r["sent"] for r in hour)
    lost = sum(r["sent"] - r["recv"] for r in hour)
    lines.append(f"Last hour: {len(hour)} packet runs, loss {100 * lost / sent:.2f} %" if sent
                 else "Last hour: no packet runs")
    lines += ["", f"{'target':<34}{'kind':<6}{'age':>6}{'loss %':>9}{'p50 ms':>9}{'p95 ms':>9}{'jitter':>9}"]
    latest = {}
    for r in loss:
        latest[(r["kind"], r["target"])] = r
    for (kind, target), r in sorted(latest.items()):
        lines.append(f"{target[:33]:<34}{kind:<6}{_age(now - r['ts']):>6}{r['loss_pct']:>9.2f}"
                     f"{_num(r['rtt_p50'], '.1f'):>9}{_num(r['rtt_p95'], '.1f'):>9}{_num(r['jitter_ms'], '.2f'):>9}")
    lines += _spark_block(loss, tput, width)
    return [ln[:width] for ln in lines]


def _latest_profile(db: Database) -> str:
    row = db.conn.execute("SELECT profile FROM runs ORDER BY ts DESC LIMIT 1").fetchone()
    return row["profile"]


def _spark_block(loss, tput, width: int) -> list[str]:
    n = max(10, width - 24)
    pk = [r for r in loss if r["kind"] in PACKET_KINDS] or list(loss)
    out = [""]
    if pk:
        out.append(f"loss %      {sparkline([r['loss_pct'] for r in pk], n)}")
        out.append(f"rtt p50 ms  {sparkline([r['rtt_p50'] for r in pk if r['rtt_p50'] is not None], n)}")
    if tput:
        out.append(f"Mbit/s      {sparkline([r['mbps'] for r in tput], n)}")
    return out


def run_tui(db_path: str, profile: str | None, interval: float = 2.0) -> None:
    if not sys.stdout.isatty():
        with Database(db_path) as db:
            print("\n".join(build_lines(db, profile)))
        return
    import curses

    def loop(scr) -> None:
        curses.curs_set(0)
        scr.timeout(int(interval * 1000))
        with Database(db_path) as db:
            while True:
                h, w = scr.getmaxyx()
                scr.erase()
                for i, line in enumerate(build_lines(db, profile, w - 1)[: h - 1]):
                    try:
                        scr.addstr(i, 0, line)
                    except curses.error:
                        pass
                scr.refresh()
                if scr.getch() in (ord("q"), ord("Q"), 27):
                    return

    try:
        curses.wrapper(loop)
    except KeyboardInterrupt:
        pass
