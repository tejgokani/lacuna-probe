"""Toolkit for the full-screen app. Nothing here imports curses.

A screen renders to `Line` values (lists of styled segments) and receives
normalized key names, so every screen can be tested without a terminal.
"""

from __future__ import annotations

import textwrap
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

Seg = tuple[str, str]  # (text, style)
Line = list[Seg]

# screen name -> key that jumps to it. The app builds its tab order from this.
NUM = {"dashboard": 1, "probes": 2, "targets": 3, "monitor": 4, "server": 5, "report": 6, "settings": 7, "help": 8}

# styles: "", title, hdr, sel, dim, ok, warn, err, key, val, tab, tabsel


def L(text: str = "", style: str = "") -> Line:
    return [(text, style)]


def plain(lines: Sequence[Line]) -> list[str]:
    return ["".join(t for t, _ in ln) for ln in lines]


def line_width(line: Line) -> int:
    return sum(len(t) for t, _ in line)


def clip(line: Line, width: int) -> Line:
    out: Line = []
    left = width
    for text, style in line:
        if left <= 0:
            break
        out.append((text[:left], style))
        left -= len(text)
    return out


def pad(line: Line, width: int, style: str = "") -> Line:
    short = width - line_width(line)
    return line + [(" " * short, style)] if short > 0 else clip(line, width)


def side_by_side(left: list[Line], right: list[Line], left_w: int, gap: int = 2) -> list[Line]:
    rows = max(len(left), len(right))
    out = []
    for i in range(rows):
        a = pad(left[i] if i < len(left) else [], left_w)
        out.append(a + [(" " * gap, "")] + (right[i] if i < len(right) else []))
    return out


def wrap_block(text: str, width: int) -> list[str]:
    """Wrap prose, keep indented or blank lines as they are."""
    out: list[str] = []
    for raw in text.split("\n"):
        if not raw.strip() or raw.startswith("  ") or raw.startswith("|"):
            out.append(raw[:width])
        else:
            out.extend(textwrap.wrap(raw, max(width, 10)) or [""])
    return out


def bar(frac: float, width: int) -> str:
    frac = min(max(frac, 0.0), 1.0)
    full = int(round(frac * width))
    return "#" * full + "." * (width - full)


# ---- keys ----

KEYS = {"enter", "esc", "tab", "btab", "up", "down", "left", "right", "pgup", "pgdn", "home", "end",
        "backspace", "delete", "f1", "resize"}


def is_printable(key: str) -> bool:
    return len(key) == 1 and key.isprintable()


class Editor:
    """Single line text input."""

    def __init__(self, value: str = ""):
        self.buf = list(value)
        self.pos = len(self.buf)

    @property
    def text(self) -> str:
        return "".join(self.buf)

    def feed(self, key: str) -> str | None:
        """Returns "commit", "cancel" or None while still editing."""
        if key == "enter":
            return "commit"
        if key == "esc":
            return "cancel"
        if key == "left":
            self.pos = max(0, self.pos - 1)
        elif key == "right":
            self.pos = min(len(self.buf), self.pos + 1)
        elif key == "home":
            self.pos = 0
        elif key == "end":
            self.pos = len(self.buf)
        elif key == "backspace" and self.pos:
            del self.buf[self.pos - 1]
            self.pos -= 1
        elif key == "delete" and self.pos < len(self.buf):
            del self.buf[self.pos]
        elif is_printable(key):
            self.buf.insert(self.pos, key)
            self.pos += 1
        return None

    def render(self, width: int) -> Line:
        start = max(0, self.pos - width + 2)
        shown = self.buf[start : start + width - 1]
        at = self.pos - start
        before, cur, after = "".join(shown[:at]), (shown[at] if at < len(shown) else " "), "".join(shown[at + 1 :])
        return [(before, "val"), (cur, "sel"), (after, "val")]


@dataclass(slots=True)
class Field:
    key: str
    label: str
    value: str = ""
    choices: list[str] | None = None
    hint: str = ""
    kind: str = "text"  # text | choice | action
    secret: bool = False


class Form:
    """A vertical list of fields. up/down move, enter edits, left/right cycle choices."""

    def __init__(self, fields: list[Field]):
        self.fields = fields
        self.cursor = 0
        self.editor: Editor | None = None

    @property
    def editing(self) -> bool:
        return self.editor is not None

    def values(self) -> dict[str, str]:
        return {f.key: f.value for f in self.fields}

    def set(self, key: str, value: str) -> None:
        for f in self.fields:
            if f.key == key:
                f.value = value

    def key(self, k: str) -> str | None:
        """Returns an event: "changed:KEY" or "action:KEY", else None."""
        f = self.fields[self.cursor]
        if self.editor is not None:
            res = self.editor.feed(k)
            if res == "commit":
                f.value = self.editor.text
                self.editor = None
                return f"changed:{f.key}"
            if res == "cancel":
                self.editor = None
            return None
        if k == "up":
            self.cursor = max(0, self.cursor - 1)
        elif k == "down":
            self.cursor = min(len(self.fields) - 1, self.cursor + 1)
        elif f.kind == "choice" and f.choices and k in ("enter", "left", "right", " "):
            step = -1 if k == "left" else 1
            f.value = f.choices[(f.choices.index(f.value) + step) % len(f.choices)] if f.value in f.choices else f.choices[0]
            return f"changed:{f.key}"
        elif f.kind == "action" and k in ("enter", " "):
            return f"action:{f.key}"
        elif f.kind == "text" and k == "enter":
            self.editor = Editor(f.value)
        return None

    def render(self, width: int, label_w: int | None = None) -> list[Line]:
        label_w = label_w or max(len(f.label) for f in self.fields) + 1
        rows: list[Line] = []
        for i, f in enumerate(self.fields):
            here = i == self.cursor
            marker = ("> ", "key") if here else ("  ", "")
            if f.kind == "action":
                rows.append([marker, (f"[ {f.label} ]", "sel" if here else "key")])
                continue
            label = (f"{f.label:<{label_w}}", "hdr" if here else "")
            if here and self.editor is not None:
                value = self.editor.render(max(10, width - label_w - 4))
            else:
                shown = "*" * len(f.value) if f.secret else f.value
                if f.kind == "choice":
                    shown = f"< {shown} >"
                value = [(shown, "val" if here else "")]
            row = [marker, label] + value
            hint = f.hint or ("Enter to change" if f.kind == "choice" else "")
            if hint and here and self.editor is None:
                row.append(("   " + hint, "dim"))
            rows.append(row)
        return rows


class Task:
    """Run a function in a thread. Poll `done`, then read `result` or `error`."""

    def __init__(self, fn: Callable[..., Any], *args: Any):
        self.result: Any = None
        self.error: BaseException | None = None
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(fn, args), daemon=True)
        self._thread.start()

    def _run(self, fn: Callable[..., Any], args: tuple) -> None:
        try:
            self.result = fn(*args)
        except BaseException as exc:  # shown to the user, never raised in the UI thread
            self.error = exc
        finally:
            self._done.set()

    @property
    def done(self) -> bool:
        return self._done.is_set()


SPINNER = "|/-\\"


def spinner(now: float) -> str:
    return SPINNER[int(now * 8) % 4]


@dataclass
class Scroller:
    """Vertical scroll state shared by long screens."""

    top: int = 0
    page: int = field(default=10)

    def key(self, k: str, total: int, height: int) -> bool:
        self.page = max(1, height - 1)
        if k == "up":
            self.top -= 1
        elif k == "down":
            self.top += 1
        elif k in ("pgup",):
            self.top -= self.page
        elif k in ("pgdn", " "):
            self.top += self.page
        elif k == "home":
            self.top = 0
        elif k == "end":
            self.top = total
        else:
            return False
        self.top = max(0, min(self.top, max(0, total - height)))
        return True

    def view(self, lines: list[Line], height: int) -> list[Line]:
        self.top = max(0, min(self.top, max(0, len(lines) - height)))
        return lines[self.top : self.top + height]
