"""Monitor and Server screens: start and stop the background services."""

from __future__ import annotations

from lacuna.config import ConfigError
from lacuna.tui.core import NUM, L, Line, Scroller
from lacuna.tui.services import Context


class MonitorScreen:
    name = "monitor"
    title = "Monitor"
    captures_text = False
    hints = "s start/stop  l reload config  arrows scroll log"

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.scroll = Scroller()
        self.error = ""
        self.follow = True  # keep the newest log lines in view
        self._total = self._h = 0

    def on_show(self) -> None:
        pass

    def tick(self, now: float) -> None:
        pass

    def key(self, k: str) -> None:
        mon = self.ctx.monitor
        if k == "s":
            if mon.running:
                mon.stop()
                self.ctx.set_status("Monitor stopping after the current probe", "warn")
                return
            try:
                mon.start()
                self.error = ""
                self.ctx.set_status("Monitor started", "ok")
            except (ConfigError, OSError, ValueError) as exc:
                self.error = str(exc)
                self.ctx.set_status("Could not start the monitor", "err")
        elif k == "l":
            try:
                mon.cfg = mon.load_config()
                self.error = ""
                self.ctx.set_status("Config reloaded. Restart the monitor to use it.", "ok")
            except (ConfigError, OSError, ValueError) as exc:
                self.error = str(exc)
        elif self.scroll.key(k, self._total, self._h):
            self.follow = k in ("end", "pgdn", "down") and self.scroll.top >= max(0, self._total - self._h)

    def render(self, w: int, h: int) -> list[Line]:
        mon = self.ctx.monitor
        state = ("RUNNING", "ok") if mon.running else ("stopped", "dim")
        lines: list[Line] = [
            [(" Monitor  ", "hdr"), (state[0], state[1]),
             (f"   {mon.runs} runs, {mon.failures} failures", "dim")],
            L(" Runs the probe jobs on a schedule and stores every result. Press s to start or stop.", "dim"),
            L(),
        ]
        if self.error:
            lines += [L(" " + self.error, "err"), L()]
        cfg = mon.cfg
        if cfg is None:
            try:
                cfg = mon.load_config()
            except (ConfigError, OSError, ValueError) as exc:
                self.error = str(exc)
                lines += [L(" " + self.error, "err")]
        if cfg is not None:
            src = mon.config_path() or "built-in defaults"
            lines.append([(" Config   ", "dim"), (src, "val")])
            lines.append([(" Profile  ", "dim"), (cfg.profile, "val"), ("   Database  ", "dim"), (str(cfg.db_path), "val")])
            if mon.target_jobs:
                lines.append(L(f" {mon.target_jobs} of the jobs below come from your saved targets (Targets screen).", "dim"))
            for msg in mon.target_problems:
                lines.append(L(" Skipped: " + msg, "warn"))
            lines.append(L())
            lines.append(L(f" {'every':>8}  job", "dim"))
            for j in cfg.jobs:
                lines.append(L(f" {j.every_s:>7g}s  {j.label}"))
        lines += [L(), L(" Log", "hdr")]
        log = list(mon.log)
        lines += [L(" " + t, "err" if "FAILED" in t or "error" in t else "") for t in log] or [L("  (nothing yet)", "dim")]
        self._total, self._h = len(lines), h
        if self.follow:
            self.scroll.top = len(lines)
        return self.scroll.view(lines, h)


class ServerScreen:
    name = "server"
    title = "Server"
    captures_text = False
    hints = "s start/stop"

    def __init__(self, ctx: Context):
        self.ctx = ctx

    def on_show(self) -> None:
        pass

    def tick(self, now: float) -> None:
        pass

    def key(self, k: str) -> None:
        if k != "s":
            return
        srv = self.ctx.server
        if srv.running:
            srv.stop()
            self.ctx.set_status("Server stopped", "warn")
            return
        try:
            srv.start()
            self.ctx.set_status("Server started", "ok")
        except OSError as exc:
            self.ctx.set_status(f"Could not start the server: {exc}", "err")

    def render(self, w: int, h: int) -> list[Line]:
        s = self.ctx.settings
        srv = self.ctx.server.server
        state = ("LISTENING", "ok") if srv else ("stopped", "dim")
        lines: list[Line] = [
            [(" Lacuna server  ", "hdr"), (state[0], state[1])],
            L(" The far end for UDP probes and TCP throughput tests. Press s to start or stop.", "dim"),
            L(),
            [(" UDP   ", "dim"), (f"{s.server_bind}:{s.server_port}", "val"), ("   echo probes", "dim")],
            [(" TCP   ", "dim"), (f"{s.server_bind}:{s.server_port + 1}", "val"), ("   throughput tests", "dim")],
            L(f" Change bind address and port in Settings ({NUM['settings']}).", "dim"),
            L(),
        ]
        if srv is not None:
            st = srv.state
            lines += [
                L(" Live", "hdr"),
                [(" Active sessions   ", "dim"), (str(len(st.sessions)), "val")],
                [(" Packets dropped   ", "dim"), (str(st.dropped), "val"),
                 ("   (bad cookie, rate cap, oversize, unknown)", "dim")],
                L(),
                L(" On the machine you probe from, run:", "dim"),
                L(f"   lacuna udp THIS.HOST.ADDRESS:{s.server_port}"),
                L(f"   lacuna throughput THIS.HOST.ADDRESS:{s.server_port}"),
                L(),
            ]
        lines += [
            L(" Safety", "hdr"),
            L("  Replies are never larger than requests, probes need a handshake cookie,", "dim"),
            L("  and each source IP is limited to 200 packets/s and 8 live sessions.", "dim"),
            L("  There is no password. Anyone who can reach these ports can run tests,", "dim"),
            L("  so firewall them if you do not want that.", "dim"),
        ]
        return lines[:h]
