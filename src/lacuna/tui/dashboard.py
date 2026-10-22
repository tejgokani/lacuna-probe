"""Dashboard screen and the plain-text snapshot used when there is no terminal."""

from __future__ import annotations

import time
from collections.abc import Sequence

from lacuna.analysis import gilbert
from lacuna.db import Database
from lacuna.demo import demo_db_path
from lacuna.report.html import Summary, profile_summary
from lacuna.tui.core import L, Line, Scroller
from lacuna.tui.services import Context
from lacuna.tui.settings import WINDOW_SECONDS, WINDOWS

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


def age(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.0f}d"


def _num(v: float | None, spec: str) -> str:
    return "-" if v is None else format(v, spec)


def latest_profile(db: Database) -> str | None:
    row = db.conn.execute("SELECT profile FROM runs ORDER BY ts DESC LIMIT 1").fetchone()
    return row["profile"] if row else None


def table_lines(loss: list, now: float) -> list[str]:
    head = f"{'target':<34}{'kind':<6}{'age':>6}{'loss %':>9}{'p50 ms':>9}{'p95 ms':>9}{'jitter':>9}"
    latest = {}
    for r in loss:
        latest[(r["kind"], r["target"])] = r
    rows = [head]
    for (kind, target), r in sorted(latest.items()):
        rows.append(f"{target[:33]:<34}{kind:<6}{age(now - r['ts']):>6}{r['loss_pct']:>9.2f}"
                    f"{_num(r['rtt_p50'], '.1f'):>9}{_num(r['rtt_p95'], '.1f'):>9}{_num(r['jitter_ms'], '.2f'):>9}")
    return rows


def spark_lines(loss: list, tput: list, width: int) -> list[str]:
    n = max(10, width - 24)
    pk = [r for r in loss if r["kind"] in PACKET_KINDS] or list(loss)
    out = []
    if pk:
        out.append(f"loss %      {sparkline([r['loss_pct'] for r in pk], n)}")
        out.append(f"rtt p50 ms  {sparkline([r['rtt_p50'] for r in pk if r['rtt_p50'] is not None], n)}")
    if tput:
        out.append(f"Mbit/s      {sparkline([r['mbps'] for r in tput], n)}")
    return out


def build_lines(db: Database, profile: str | None, width: int = 100, now: float | None = None) -> list[str]:
    """Plain snapshot of the dashboard. Used by `lacuna tui` without a terminal."""
    now = time.time() if now is None else now
    if profile is None:
        profile = latest_profile(db)
        if profile is None:
            return ["No data yet. Run `lacuna monitor` or a probe command first."]
    loss, tput = db.loss_rows([profile]), db.tput_rows([profile])
    stamp = time.strftime("%H:%M:%S", time.localtime(now))
    lines = [f"Lacuna live view   profile: {profile}   {stamp}", ""]
    if not loss and not tput:
        return lines + ["No samples for this profile yet."]
    hour = [r for r in loss if now - r["ts"] <= 3600 and r["kind"] in PACKET_KINDS]
    sent = sum(r["sent"] for r in hour)
    lost = sum(r["sent"] - r["recv"] for r in hour)
    lines.append(f"Last hour: {len(hour)} packet runs, loss {100 * lost / sent:.2f} %" if sent
                 else "Last hour: no packet runs")
    lines += [""] + table_lines(loss, now) + [""] + spark_lines(loss, tput, width)
    return [ln[:width] for ln in lines]


def severity(loss_pct: float | None) -> str:
    if loss_pct is None:
        return "dim"
    return "ok" if loss_pct < 1 else "warn" if loss_pct < 5 else "err"


class DashboardScreen:
    name = "dashboard"
    title = "Dashboard"
    captures_text = False
    hints = "p profile  w window  d demo data  r refresh  arrows scroll"

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.profile: str | None = None
        self.profiles: list[str] = []
        self.summary: Summary | None = None
        self.loss: list = []
        self.tput: list = []
        self.error = ""
        self.window = self._seen = ctx.settings.window
        self.scroll = Scroller()
        self._last = 0.0
        self._total = self._h = 0
        self.refresh()

    # ---- data ----

    def since(self) -> float | None:
        secs = WINDOW_SECONDS[self.window]
        return None if secs is None else time.time() - secs

    def refresh(self) -> None:
        self._last = time.monotonic()
        try:
            with self.ctx.open_db() as db:
                self.profiles = db.profiles()
                if self.profile not in self.profiles:
                    self.profile = latest_profile(db)
                if self.profile is None:
                    self.summary, self.loss, self.tput = None, [], []
                    self.error = ""
                    return
                since = self.since()
                self.loss = db.loss_rows([self.profile], since)
                self.tput = db.tput_rows([self.profile], since)
                self.summary = profile_summary(self.profile, self.loss, self.tput)
                self.error = ""
        except Exception as exc:
            self.error = f"cannot read {self.ctx.db_path()}: {exc}"

    def tick(self, now: float) -> None:
        if time.monotonic() - self._last >= self.ctx.settings.refresh_s:
            self.refresh()

    def on_show(self) -> None:
        if self.ctx.settings.window != self._seen:  # changed in Settings
            self.window = self._seen = self.ctx.settings.window
        self.refresh()

    # ---- input ----

    def key(self, k: str) -> None:
        if k in ("p", "P") and self.profiles:
            step = 1 if k == "p" else -1
            i = self.profiles.index(self.profile) if self.profile in self.profiles else 0
            self.profile = self.profiles[(i + step) % len(self.profiles)]
            self.refresh()
        elif k == "w":
            self.window = WINDOWS[(WINDOWS.index(self.window) + 1) % len(WINDOWS)]
            self.refresh()
        elif k == "d":
            if not self.ctx.use_demo and not demo_db_path().exists():
                self.ctx.set_status("No demo data yet. Run the offline demo from the Probes screen.", "warn")
                return
            self.ctx.use_demo = not self.ctx.use_demo
            self.profile = None
            self.refresh()
            self.ctx.set_status("Showing demo data (simulated)" if self.ctx.use_demo else "Showing your data")
        elif k == "r":
            self.refresh()
        else:
            self.scroll.key(k, self._total, self._h)

    # ---- drawing ----

    def render(self, w: int, h: int) -> list[Line]:
        now = time.time()
        src = "DEMO (simulated)" if self.ctx.use_demo else "your data"
        head: list[Line] = [[(" Profile ", "dim"), (self.profile or "none", "val"), ("   Window ", "dim"),
                             (self.window, "val"), ("   Source ", "dim"),
                             (src, "warn" if self.ctx.use_demo else "val"),
                             ("   updated " + time.strftime("%H:%M:%S"), "dim")], L()]
        if self.error:
            return head + [L(" " + self.error, "err")]
        if self.summary is None or not (self.loss or self.tput):
            return head + self._empty()
        full = head + self._cards(self.summary) + self._table(now, w)
        self._total, self._h = len(full), h
        return self.scroll.view(full, h)

    def _empty(self) -> list[Line]:
        msgs = [
            "Nothing measured in this window yet.",
            "",
            "  2  Probes   run a ping, UDP, TCP or throughput test now",
            "  3  Monitor  keep probing on a schedule in the background",
            "  2  Probes   choose 'Offline demo' to see how everything looks (about 40 s)",
            "  7  Help     read how Lacuna works",
            "",
            "Press w to widen the time window, or p to pick another profile.",
        ]
        return [L(" " + m, "dim" if m.startswith("  ") else "") for m in msgs]

    def _cards(self, s: Summary) -> list[Line]:
        loss = f"{s.loss_pct:.2f} %" if s.loss_pct is not None else "n/a"
        rtt = f"{s.rtt_p95:.1f} ms" if s.rtt_p95 is not None else "n/a"
        jit = f"{s.jitter_ms:.2f} ms" if s.jitter_ms is not None else "n/a"
        tp = f"{s.tput_median:.1f} Mbit/s" if s.tput_median is not None else "n/a"
        out: list[Line] = [
            [(" Loss        ", "dim"), (f"{loss:<14}", severity(s.loss_pct)), (f"{s.lost} of {s.sent} packets", "dim")],
            [(" RTT p95     ", "dim"), (f"{rtt:<14}", "val"), (f"jitter {jit}", "dim")],
            [(" Throughput  ", "dim"), (f"{tp:<14}", "val"), (f"{s.tput_samples} test(s)", "dim")],
            [(" Longest burst ", "dim"), (str(s.longest_burst if s.longest_burst is not None else "n/a"), "val"),
             (" packets", "dim")],
        ]
        if s.ge is not None and s.ge.p is not None and s.ge.r:
            out.append([(" Gilbert-Elliott ", "dim"),
                        (f"p {s.ge.p:.4f}  r {s.ge.r:.3f}  mean burst {1 / s.ge.r:.1f}", "val"),
                        ("" if gilbert.MIN_RELIABLE <= s.ge.n else "  (small sample)", "dim")])
        out += [L(), L(" " + s.verdict, "hdr"), L()]
        return out

    def _table(self, now: float, w: int) -> list[Line]:
        rows = table_lines(self.loss, now)
        out: list[Line] = [L(" " + rows[0], "dim")]
        for r in rows[1:]:
            out.append(L(" " + r))
        out.append(L())
        for r in spark_lines(self.loss, self.tput, w):
            out.append(L(" " + r, "val"))
        return out
