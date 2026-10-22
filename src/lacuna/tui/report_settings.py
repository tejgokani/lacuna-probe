"""Report and Settings screens."""

from __future__ import annotations

import webbrowser
from pathlib import Path

from lacuna import export
from lacuna.db import parse_since
from lacuna.report.html import build_report
from lacuna.tui import settings as cfg
from lacuna.tui.core import NUM, Field, Form, L, Line
from lacuna.tui.services import Context


class ReportScreen:
    name = "report"
    title = "Report"
    captures_text = False
    hints = "up/down profile  space toggle  a all  w window  r HTML  c CSV  j JSON  o open last"

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.profiles: list[tuple[str, int]] = []  # (name, runs)
        self.chosen: set[str] = set()
        self.cursor = 0
        self.window = ctx.settings.window
        self.last: Path | None = None
        self.message: tuple[str, str] | None = None

    def tick(self, now: float) -> None:
        pass

    def on_show(self) -> None:
        try:
            with self.ctx.open_db() as db:
                rows = db.conn.execute("SELECT profile, COUNT(*) n FROM runs GROUP BY profile ORDER BY profile")
                self.profiles = [(r["profile"], r["n"]) for r in rows.fetchall()]
        except Exception as exc:
            self.profiles = []
            self.message = (f"cannot read the database: {exc}", "err")
        names = {n for n, _ in self.profiles}
        self.chosen = (self.chosen & names) or names
        self.cursor = min(self.cursor, max(0, len(self.profiles) - 1))

    def _since(self) -> float | None:
        secs = cfg.WINDOW_SECONDS[self.window]
        return None if secs is None else parse_since(f"{int(secs)}s")

    def _out(self, name: str) -> Path:
        d = self.ctx.settings.effective_output_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d / name

    def key(self, k: str) -> None:
        n = len(self.profiles)
        if k == "up":
            self.cursor = max(0, self.cursor - 1)
        elif k == "down":
            self.cursor = min(max(0, n - 1), self.cursor + 1)
        elif k == " " and n:
            name = self.profiles[self.cursor][0]
            self.chosen ^= {name}
        elif k == "a":
            self.chosen = {p for p, _ in self.profiles}
        elif k == "w":
            self.window = cfg.WINDOWS[(cfg.WINDOWS.index(self.window) + 1) % len(cfg.WINDOWS)]
        elif k in ("r", "c", "j"):
            self._build(k)
        elif k == "o":
            self._open()

    def _build(self, k: str) -> None:
        if not self.chosen:
            self.message = ("Select at least one profile (space).", "warn")
            return
        names = sorted(self.chosen)
        try:
            with self.ctx.open_db() as db:
                if k == "r":
                    path = self._out("lacuna-report.html")
                    path.write_text(build_report(db, names, self._since()), encoding="utf-8")
                    self.last = path
                else:
                    path = self._out("lacuna-export." + ("csv" if k == "c" else "json"))
                    fn = export.to_csv if k == "c" else export.to_json
                    path.write_text(fn(db, names, self._since()), encoding="utf-8")
        except OSError as exc:
            self.message = (f"Could not write the file: {exc}", "err")
            return
        self.message = (f"Wrote {path}", "ok")
        self.ctx.set_status("Saved " + path.name, "ok")

    def _open(self) -> None:
        if self.last is None:
            self.message = ("Build a report first (r).", "warn")
            return
        ok = webbrowser.open(self.last.resolve().as_uri())
        self.message = (("Opened in your browser: " if ok else "No browser available. Open it yourself: ") + str(self.last), "ok" if ok else "warn")

    def render(self, w: int, h: int) -> list[Line]:
        s = self.ctx.settings
        lines: list[Line] = [
            L(" Build a single-file HTML report, or export the raw samples.", "dim"),
            L(),
            L(" Profiles to include", "hdr"),
        ]
        if not self.profiles:
            lines.append(L("  No data yet. Run a probe first (Probes screen).", "dim"))
        for i, (name, n) in enumerate(self.profiles):
            mark = "[x]" if name in self.chosen else "[ ]"
            here = i == self.cursor
            lines.append([("> " if here else "  ", "key"), (f"{mark} {name}", "sel" if here else ""), (f"   {n} runs", "dim")])
        lines += [
            L(),
            [(" Window  ", "dim"), (self.window, "val"), ("   Save to  ", "dim"), (str(s.effective_output_dir()), "val")],
            L(f" Change the folder in Settings ({NUM['settings']}).", "dim"),
            L(),
            [(" r ", "key"), ("HTML report   ", ""), ("c ", "key"), ("CSV   ", ""), ("j ", "key"), ("JSON   ", ""),
             ("o ", "key"), ("open last report in browser", "")],
        ]
        if self.message:
            lines += [L(), L(" " + self.message[0], self.message[1])]
        return lines[:h]


class SettingsScreen:
    name = "settings"
    title = "Settings"
    hints = "up/down move  Enter edit or change  Esc cancel edit"

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.message: tuple[str, str] | None = None
        self.form = self._make_form()

    @property
    def captures_text(self) -> bool:
        return self.form.editing

    def tick(self, now: float) -> None:
        pass

    def on_show(self) -> None:
        self.form = self._make_form()

    def _make_form(self) -> Form:
        s = self.ctx.settings
        return Form([
            Field("profile", "Profile name", s.profile, hint=f"empty = hostname ({cfg.default_profile()})"),
            Field("db_path", "Database file", s.db_path, hint=f"empty = {cfg.default_db_path()}"),
            Field("window", "Default window", s.window, list(cfg.WINDOWS), kind="choice"),
            Field("refresh_s", "Refresh (seconds)", f"{s.refresh_s:g}", hint="0.5 to 60"),
            Field("config_path", "Monitor config", s.config_path, hint="empty = ~/.config/lacuna/lacuna.toml"),
            Field("server_bind", "Server bind", s.server_bind, hint="0.0.0.0 = all, 127.0.0.1 = this machine"),
            Field("server_port", "Server port", str(s.server_port), hint="UDP; TCP uses port + 1"),
            Field("output_dir", "Save reports to", s.output_dir, hint="empty = current folder"),
            Field("color", "Colors", "on" if s.color else "off", ["on", "off"], kind="choice"),
            Field("reset", "Reset all settings to defaults", kind="action"),
        ])

    def key(self, k: str) -> None:
        event = self.form.key(k)
        if event is None:
            return
        kind, _, key = event.partition(":")
        if key == "reset":
            self.ctx.settings = cfg.Settings()
            cfg.save(self.ctx.settings)
            self.form = self._make_form()
            self.message = ("Settings reset.", "ok")
            return
        value = next(f.value for f in self.form.fields if f.key == key)
        try:
            cfg.apply(self.ctx.settings, key, value)
        except (ValueError, OSError) as exc:
            self.message = (str(exc), "err")
            self.form = self._make_form()  # drop the rejected value
            return
        self.message = (f"Saved {key.replace('_', ' ')}.", "ok")
        if key in ("db_path",):
            self.ctx.set_status("Database changed. The Dashboard will use it now.", "ok")

    def render(self, w: int, h: int) -> list[Line]:
        lines: list[Line] = [
            L(" Settings are saved as you change them to:", "dim"),
            L("   " + str(cfg.settings_path()), "val"),
            L(" They apply to this app. CLI flags and config files are separate.", "dim"),
            L(),
        ]
        lines += self.form.render(w, 20)
        if self.message:
            lines += [L(), L(" " + self.message[0], self.message[1])]
        return lines[:h]
