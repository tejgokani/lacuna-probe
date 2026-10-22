"""Probes screen: run one probe, or the offline demo, from a form."""

from __future__ import annotations

import textwrap
import time
from pathlib import Path

from lacuna.config import Job
from lacuna.db import Database, LossSample
from lacuna.demo import demo_db_path, run_demo
from lacuna.fmt import format_loss, format_tput
from lacuna.jobs import JOB_KIND, build_job  # noqa: F401
from lacuna.monitor import Outcome, run_job, store
from lacuna.protocol import MAX_UDP_COUNT
from lacuna.tui.core import Field, Form, L, Line, Scroller, Task, spinner
from lacuna.tui.services import Context

URL = "https://speed.cloudflare.com/__down?bytes=10000000"

# kind -> (label, [(key, label, default, hint)])
SPECS: dict[str, tuple[str, list[tuple[str, str, str, str]]]] = {
    "ping": ("Ping (ICMP)", [
        ("target", "Target", "1.1.1.1", "host or IPv4"),
        ("count", "Packets", "20", ""),
        ("interval", "Interval (s)", "0.2", "0.2 = 5 pps, cap 20 pps"),
    ]),
    "udp": ("UDP probe", [
        ("target", "Server HOST:PORT", "", "a running `lacuna server`"),
        ("count", "Packets", "500", f"up to {MAX_UDP_COUNT}"),
        ("pps", "Packets/s", "20", "cap 200"),
        ("size", "Packet bytes", "200", "32 to 1400"),
    ]),
    "tcp": ("TCP connect", [
        ("target", "Target HOST:PORT", "1.1.1.1:443", ""),
        ("count", "Connects", "20", ""),
        ("interval", "Interval (s)", "0.5", "cap 10 per second"),
    ]),
    "throughput": ("Throughput (Lacuna server)", [
        ("target", "Server HOST:PORT", "", "UDP port of the server, TCP is +1"),
        ("secs", "Seconds", "10", "max 60"),
        ("streams", "Streams", "1", "1 to 8"),
        ("direction", "Direction", "down", ""),
    ]),
    "http": ("Throughput (HTTP download)", [
        ("target", "URL", URL, ""),
        ("secs", "Seconds", "10", "max 60"),
        ("streams", "Streams", "4", "1 to 16"),
    ]),
    "demo": ("Offline demo", []),
}
KINDS = list(SPECS)
def _probe_and_store(job: Job, profile: str, db_path: Path) -> tuple[Job, Outcome]:
    out = run_job(job)
    with Database(db_path) as db:
        store(db, profile, job, out)
    return job, out


class ProbesScreen:
    name = "probes"
    title = "Probes"
    hints = "up/down field  Enter edit, choose or run  r run  pgup/pgdn result"

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.kind = "ping"
        self.saved: dict[str, dict[str, str]] = {}
        self.form = self._make_form()
        self.task: Task | None = None
        self.task_started = 0.0
        self.result: list[str] = []
        self.result_style = ""
        self.demo_log: list[str] = []
        self.scroll = Scroller()
        self._total = self._h = 0

    background = True

    @property
    def captures_text(self) -> bool:
        return self.form.editing

    # ---- form ----

    def _make_form(self) -> Form:
        fields = [Field("kind", "Probe", SPECS[self.kind][0], [SPECS[k][0] for k in KINDS], kind="choice")]
        mem = self.saved.get(self.kind, {})
        for key, label, default, hint in SPECS[self.kind][1]:
            if key == "direction":
                fields.append(Field(key, label, mem.get(key, default), ["down", "up"], kind="choice"))
            else:
                fields.append(Field(key, label, mem.get(key, default), hint=hint))
        fields.append(Field("run", "Run now" if self.kind != "demo" else "Run offline demo (about 40 s)",
                            kind="action"))
        return Form(fields)

    def _remember(self) -> None:
        self.saved[self.kind] = {f.key: f.value for f in self.form.fields if f.kind != "action"}

    def on_show(self) -> None:
        srv = self.ctx.server.server
        for kind in ("udp", "throughput"):
            mem = self.saved.setdefault(kind, {})
            if srv is not None and not mem.get("target"):
                mem["target"] = f"127.0.0.1:{srv.port}"
        if self.kind in ("udp", "throughput") and not self.form.values().get("target") and srv is not None:
            self.form.set("target", f"127.0.0.1:{srv.port}")

    def tick(self, now: float) -> None:
        if self.task is not None and self.task.done:
            task, self.task = self.task, None
            self._finish(task)

    # ---- running ----

    def _start(self) -> None:
        if self.task is not None:
            self.ctx.set_status("A probe is already running", "warn")
            return
        values = self.form.values()
        self.result, self.result_style = [], ""
        try:
            if self.kind == "demo":
                self.demo_log = []
                out = demo_db_path().parent / "demo-report.html"
                self.task = Task(self._demo, out)
            else:
                job = build_job(self.kind, values)
                self.task = Task(_probe_and_store, job, self.ctx.settings.effective_profile(), self.ctx.db_path_real())
        except ValueError as exc:
            self.result, self.result_style = [str(exc)], "err"
            return
        self.task_started = time.monotonic()

    def _demo(self, out: Path) -> Path:
        return run_demo(out, self.ctx.settings.server_port, log=self.demo_log.append)

    def _finish(self, task: Task) -> None:
        err = task.error
        if err is not None:
            msg = str(err) or type(err).__name__
            if isinstance(err, OSError) and "in use" in msg.lower():
                msg += f". The demo needs ports {self.ctx.settings.server_port} to {self.ctx.settings.server_port + 11} free. Stop the Server screen's server first."
            self.result, self.result_style = [msg], "err"
            self.ctx.set_status("Probe failed", "err")
            return
        if self.kind == "demo":
            self.result = self.demo_log + ["", f"Report written to {task.result}",
                                           "Press 1, then d, to look at the demo data on the Dashboard."]
            self.ctx.set_status("Demo finished. Data is simulated.", "ok")
            return
        job, out = task.result
        s = out.sample
        profile = self.ctx.settings.effective_profile()
        text = (format_loss(job, profile, s) if isinstance(s, LossSample)
                else format_tput(job, profile, s, out.params.get("per_stream_mbps", [])))
        self.result, self.result_style = text.split("\n"), ""
        self.ctx.set_status("Done. Result stored in the database.", "ok")

    # ---- input ----

    def key(self, k: str) -> None:
        if not self.form.editing:
            if k == "r":
                self._start()
                return
            if k in ("pgup", "pgdn", "home", "end") and self.scroll.key(k, self._total, self._h):
                return
        event = self.form.key(k)
        if event == "changed:kind":
            self._remember()
            label = self.form.fields[0].value
            self.kind = next(kd for kd in KINDS if SPECS[kd][0] == label)
            self.form = self._make_form()
            self.on_show()
        elif event == "action:run":
            self._start()

    # ---- drawing ----

    def render(self, w: int, h: int) -> list[Line]:
        now = time.monotonic()
        lines: list[Line] = [L(" Run a probe now. Results are stored and show up on the Dashboard.", "dim"), L()]
        lines += self.form.render(w)
        lines.append(L())
        if self.task is not None:
            lines.append(L(f" {spinner(now)} running for {now - self.task_started:.0f} s ...", "warn"))
            tail = self.demo_log[-8:] if self.kind == "demo" else []
            lines += [L("   " + t, "dim") for t in tail]
        elif self.result:
            style = self.result_style
            lines.append(L(" Result" if not style else " Problem", "hdr" if not style else "err"))
            for t in self.result:
                lines += [L("  " + part, style) for part in (textwrap.wrap(t, max(20, w - 4)) or [""])]
        elif self.kind == "demo":
            lines.append(L(" Starts a local server and a loss simulator, runs 12 UDP tests and 2 TCP tests,", "dim"))
            lines.append(L(" then writes a report. No internet needed. All data is simulated.", "dim"))
        self._total, self._h = len(lines), h
        return self.scroll.view(lines, h)
