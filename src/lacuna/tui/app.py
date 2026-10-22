"""App shell: tab bar, status bar, key routing, and the curses driver."""

from __future__ import annotations

import os
import time

from lacuna import __version__
from lacuna.tui import settings as settings_mod
from lacuna.tui.core import L, Line, clip, pad
from lacuna.tui.dashboard import DashboardScreen
from lacuna.tui.help_screen import HelpScreen
from lacuna.tui.monitor_server import MonitorScreen, ServerScreen
from lacuna.tui.probes import ProbesScreen
from lacuna.tui.report_settings import ReportScreen, SettingsScreen
from lacuna.tui.services import Context

MIN_W, MIN_H = 60, 14
STATUS_SECONDS = 5.0


class App:
    """All app logic. `compose()` returns the screen as styled lines, `handle()` takes key names."""

    def __init__(self, ctx: Context | None = None):
        self.ctx = ctx or Context(settings_mod.load())
        self.screens = [DashboardScreen(self.ctx), ProbesScreen(self.ctx), MonitorScreen(self.ctx),
                        ServerScreen(self.ctx), ReportScreen(self.ctx), SettingsScreen(self.ctx),
                        HelpScreen(self.ctx)]
        self.current = 0
        self.quit = False
        self.ctx.switch = self.go
        self.screens[0].on_show()

    @property
    def screen(self):
        return self.screens[self.current]

    def go(self, name: str) -> None:
        for i, s in enumerate(self.screens):
            if s.name == name:
                self.current = i
                s.on_show()
                return

    def _step(self, delta: int) -> None:
        self.current = (self.current + delta) % len(self.screens)
        self.screen.on_show()

    # ---- input ----

    def handle(self, key: str) -> None:
        if key == "resize":
            return
        if key == "ctrl-c":
            self.quit = True
            return
        if self.screen.captures_text:
            self.screen.key(key)
            return
        if key == "q":
            self.quit = True
        elif key in ("?", "f1"):
            self.go("help")
        elif len(key) == 1 and key in "1234567":
            self.current = int(key) - 1
            self.screen.on_show()
        elif key == "tab":
            self._step(1)
        elif key == "btab":
            self._step(-1)
        else:
            self.screen.key(key)

    def tick(self) -> None:
        for s in self.screens:
            if s is self.screen:
                s.tick(time.time())
            elif s.name == "probes":
                s.tick(time.time())  # let a finished probe report even when you look elsewhere

    def shutdown(self) -> None:
        self.ctx.shutdown()

    # ---- drawing ----

    def tab_bar(self, w: int) -> Line:
        compact = w < 96
        bar: Line = [(" lacuna ", "title")]
        for i, s in enumerate(self.screens):
            label = s.title[:4] if compact and i != self.current else s.title
            live = ""
            if s.name == "monitor" and self.ctx.monitor.running:
                live = " *"
            if s.name == "server" and self.ctx.server.running:
                live = " *"
            bar.append((f" {i + 1} {label}{live} ", "tabsel" if i == self.current else "tab"))
        return pad(clip(bar, w), w, "tab")

    def status_bar(self, w: int) -> Line:
        st = self.ctx.status
        if st and time.monotonic() - st[2] < STATUS_SECONDS:
            return pad(clip(L(" " + st[0], st[1]), w), w)
        return pad(clip(L(" " + self.screen.hints, "dim"), w), w)

    def footer(self, w: int) -> Line:
        text = " ? help   Tab next   1-7 screens   q quit"
        right = f"lacuna {__version__} "
        gap = max(1, w - len(text) - len(right))
        return clip([(text, "key"), (" " * gap, ""), (right, "dim")], w)

    def compose(self, w: int, h: int) -> list[Line]:
        if w < MIN_W or h < MIN_H:
            msg = [L(f" Terminal too small: {w}x{h}.", "warn"), L(f" Lacuna needs at least {MIN_W}x{MIN_H}.", "dim"),
                   L(" Make the window larger, or press q to quit.", "dim")]
            return (msg + [L()] * h)[:h]
        body_h = h - 3
        body = [clip(ln, w) for ln in self.screen.render(w, body_h)][:body_h]
        body += [L()] * (body_h - len(body))
        return [self.tab_bar(w)] + body + [self.status_bar(w), self.footer(w)]


# ---- curses driver ----

KEYMAP = {
    "KEY_UP": "up", "KEY_DOWN": "down", "KEY_LEFT": "left", "KEY_RIGHT": "right",
    "KEY_PPAGE": "pgup", "KEY_NPAGE": "pgdn", "KEY_HOME": "home", "KEY_END": "end",
    "KEY_BACKSPACE": "backspace", "KEY_DC": "delete", "KEY_BTAB": "btab", "KEY_F(1)": "f1",
    "KEY_RESIZE": "resize", "KEY_ENTER": "enter",
}


def normalize(ch) -> str | None:
    """Map a curses get_wch result to a key name."""
    import curses

    if isinstance(ch, int):
        return KEYMAP.get(curses.keyname(ch).decode("ascii", "replace"))
    if ch in ("\n", "\r"):
        return "enter"
    if ch == "\t":
        return "tab"
    if ch == "\x1b":
        return "esc"
    if ch in ("\x7f", "\b"):
        return "backspace"
    if ch == "\x03":
        return "ctrl-c"
    return ch if ch.isprintable() else None


def _styles(color: bool) -> dict[str, int]:
    import curses

    attrs = {"": 0, "title": curses.A_BOLD, "hdr": curses.A_BOLD, "sel": curses.A_REVERSE,
             "dim": curses.A_DIM, "ok": 0, "warn": curses.A_BOLD, "err": curses.A_BOLD, "key": curses.A_BOLD,
             "val": 0, "tab": curses.A_REVERSE | curses.A_DIM, "tabsel": curses.A_BOLD}
    if color and curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        for i, (fg, name) in enumerate(
            [(curses.COLOR_CYAN, "title"), (curses.COLOR_GREEN, "ok"), (curses.COLOR_YELLOW, "warn"),
             (curses.COLOR_RED, "err"), (curses.COLOR_CYAN, "val"), (curses.COLOR_CYAN, "key"),
             (curses.COLOR_CYAN, "hdr")], start=1):
            curses.init_pair(i, fg, -1)
            attrs[name] = attrs[name] | curses.color_pair(i)
        curses.init_pair(9, curses.COLOR_BLACK, curses.COLOR_CYAN)
        attrs["tabsel"] = curses.color_pair(9) | curses.A_BOLD
    return attrs


def draw(scr, lines: list[Line], attrs: dict[str, int]) -> None:
    import curses

    h, w = scr.getmaxyx()
    scr.erase()
    for y, line in enumerate(lines[:h]):
        x = 0
        for text, style in line:
            room = w - x - (1 if y == h - 1 else 0)
            if room <= 0:
                break
            try:
                scr.addnstr(y, x, text, room, attrs.get(style, 0))
            except curses.error:
                pass
            x += len(text)
    scr.refresh()


def _loop(scr, app: App) -> None:
    import curses

    curses.curs_set(0)
    scr.keypad(True)
    scr.timeout(150)
    color = app.ctx.settings.color
    attrs = _styles(color)
    while not app.quit:
        if app.ctx.settings.color != color:
            color = app.ctx.settings.color
            attrs = _styles(color)
        h, w = scr.getmaxyx()
        draw(scr, app.compose(w, h), attrs)
        try:
            ch = scr.get_wch()
        except curses.error:
            ch = None
        except KeyboardInterrupt:
            break
        if ch is not None:
            key = normalize(ch)
            if key:
                app.handle(key)
        app.tick()


def run_app(db_path: str | None = None, profile: str | None = None) -> int:
    """Open the full-screen app. `db_path` and `profile` apply to this session only."""
    import curses
    from pathlib import Path

    os.environ.setdefault("ESCDELAY", "25")
    ctx = Context(settings_mod.load())
    if db_path:
        ctx.db_override = Path(db_path).expanduser()
    app = App(ctx)
    if profile:
        app.screens[0].profile = profile
        app.screens[0].refresh()
    try:
        curses.wrapper(_loop, app)
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()
    return 0
