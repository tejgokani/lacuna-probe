"""Targets screen: saved addresses with your own operations, run by hand or on a schedule."""

from __future__ import annotations

import time

from lacuna import targets as T
from lacuna.db import Database, LossSample
from lacuna.fmt import format_loss, format_tput
from lacuna.monitor import run_job, store
from lacuna.tui.core import Editor, Field, Form, L, Line, Scroller, Task, spinner
from lacuna.tui.services import Context


def _run_ops(target: T.Target, ops: list[T.Op], profile: str, db_path) -> list[str]:
    """Run operations one after another. Each result or error becomes a block of text."""
    out: list[str] = []
    for i, op in enumerate(ops, 1):
        out.append(f"[{i}] {op.summary()}")
        try:
            job = T.op_job(target, op)
            outcome = run_job(job)
            with Database(db_path) as db:
                store(db, profile, job, outcome)
            s = outcome.sample
            text = (format_loss(job, profile, s) if isinstance(s, LossSample)
                    else format_tput(job, profile, s, outcome.params.get("per_stream_mbps", [])))
            out += ["    " + ln for ln in text.split("\n")]
        except Exception as exc:
            out.append(f"    FAILED: {exc}")
        out.append("")
    return out


class TargetsScreen:
    name = "targets"
    title = "Targets"
    background = True  # finished runs report even while you look at another screen
    hints = ""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.targets: list[T.Target] = T.load()
        self.mode = "list"  # list | new | detail | op
        self.cur = 0  # selected target
        self.row = 0  # selected row in detail mode
        self.edit: Editor | None = None
        self.form: Form | None = None
        self.op: T.Op | None = None  # operation being edited
        self.op_index: int | None = None  # None = new operation
        self.confirm: str | None = None
        self.message: tuple[str, str] | None = None
        self.task: Task | None = None
        self.started = 0.0
        self.result: list[str] = []
        self.scroll = Scroller()
        self._total = self._h = 0

    # ---- state ----

    @property
    def captures_text(self) -> bool:
        return self.edit is not None or (self.form is not None and self.form.editing)

    @property
    def target(self) -> T.Target | None:
        return self.targets[self.cur] if 0 <= self.cur < len(self.targets) else None

    def on_show(self) -> None:
        self.targets = T.load()
        self.cur = min(self.cur, max(0, len(self.targets) - 1))

    def _save(self) -> bool:
        try:
            T.save(self.targets)
            return True
        except OSError as exc:
            self.message = (f"Could not save targets: {exc}", "err")
            return False

    @property
    def hint_text(self) -> str:
        return {
            "list": "up/down select  Enter open  n new  r run all  d delete",
            "new": "up/down field  Enter edit or create  Esc cancel",
            "detail": "up/down row  Enter edit  r run  R run all  x delete op  PgUp/PgDn results  Esc back",
            "op": "up/down field  Enter edit or choose  r run once  Esc cancel",
        }[self.mode]

    # ---- running ----

    def tick(self, now: float) -> None:
        if self.task is not None and self.task.done:
            task, self.task = self.task, None
            if task.error is not None:
                self.result = [f"FAILED: {task.error}"]
                self.ctx.set_status("Run failed", "err")
            else:
                self.result = task.result
                self.ctx.set_status("Done. Results stored in the database.", "ok")

    def _run(self, target: T.Target, ops: list[T.Op]) -> None:
        if self.task is not None:
            self.ctx.set_status("A run is already in progress", "warn")
            return
        if not ops:
            self.message = ("This target has no operations yet. Open it and add one.", "warn")
            return
        self.result = []
        self.started = time.monotonic()
        self.task = Task(_run_ops, target, list(ops), self.ctx.settings.effective_profile(), self.ctx.db_path_real())

    # ---- input ----

    def key(self, k: str) -> None:
        self.message = None
        if k in ("pgup", "pgdn") and not self.captures_text:
            self.scroll.key(k, self._total, self._h)
            return
        if self.confirm:
            self._confirm(k)
        elif self.mode == "list":
            self._key_list(k)
        elif self.mode == "new":
            self._key_new(k)
        elif self.mode == "detail":
            self._key_detail(k)
        else:
            self._key_op(k)

    def _confirm(self, k: str) -> None:
        what, self.confirm = self.confirm, None
        if k != "y":
            self.message = ("Cancelled.", "dim")
            return
        if what == "target" and self.target:
            name = self.target.name
            del self.targets[self.cur]
            self.cur = max(0, self.cur - 1)
            if self._save():
                self.message = (f"Deleted {name}.", "ok")
        elif what == "op" and self.target and self.row - 3 < len(self.target.ops):
            del self.target.ops[self.row - 3]
            self.row = max(0, self.row - 1)
            if self._save():
                self.message = ("Operation deleted.", "ok")

    # list mode
    def _key_list(self, k: str) -> None:
        n = len(self.targets)
        if k == "up":
            self.cur = max(0, self.cur - 1)
        elif k == "down":
            self.cur = min(max(0, n - 1), self.cur + 1)
        elif k == "n":
            self.mode = "new"
            self.form = Form([Field("name", "Name"), Field("host", "Address", hint="IPv4 or host name"),
                              Field("notes", "Notes", hint="optional"), Field("create", "Create target", kind="action")])
            self.form.fields[0].value = ""
        elif k in ("enter", "right") and n:
            self.mode, self.row = "detail", 0
        elif k == "r" and self.target:
            self._run(self.target, self.target.ops)
        elif k == "d" and self.target:
            self.confirm = "target"

    # new target
    def _key_new(self, k: str) -> None:
        form = self.form
        assert form is not None
        if not form.editing and k == "esc":
            self.mode, self.form = "list", None
            return
        event = form.key(k)
        if event != "action:create":
            return
        v = form.values()
        try:
            host = T.check_host(v["host"])
            name = v["name"].strip()
            if not name:
                raise ValueError("enter a name")
            if T.find(self.targets, name):
                raise ValueError(f"a target named {name} already exists")
        except ValueError as exc:
            self.message = (str(exc), "err")
            return
        self.targets.append(T.Target(name, host, v["notes"].strip(), [T.Op("ping", "Reachability", {}, "")]))
        self.cur = len(self.targets) - 1
        self.mode, self.form, self.row = "detail", None, 3
        if self._save():
            self.message = ("Created with a starter ping operation. Edit or add more below.", "ok")

    # detail mode
    def _rows(self) -> int:
        t = self.target
        return 3 + (len(t.ops) if t else 0) + 1

    def _key_detail(self, k: str) -> None:
        t = self.target
        if t is None:
            self.mode = "list"
            return
        if self.edit is not None:
            res = self.edit.feed(k)
            if res == "commit":
                self._commit_field(t, self.edit.text)
            if res is not None:
                self.edit = None
            return
        if k == "up":
            self.row = max(0, self.row - 1)
        elif k == "down":
            self.row = min(self._rows() - 1, self.row + 1)
        elif k in ("esc", "left"):
            self.mode = "list"
        elif k == "R":
            self._run(t, t.ops)
        elif k == "r" and 3 <= self.row < 3 + len(t.ops):
            self._run(t, [t.ops[self.row - 3]])
        elif k == "x" and 3 <= self.row < 3 + len(t.ops):
            self.confirm = "op"
        elif k == "enter":
            self._enter_row(t)

    def _enter_row(self, t: T.Target) -> None:
        if self.row < 3:
            self.edit = Editor((t.name, t.host, t.notes)[self.row])
        elif self.row < 3 + len(t.ops):
            self._open_op(t, self.row - 3)
        else:
            self._open_op(t, None)

    def _commit_field(self, t: T.Target, text: str) -> None:
        text = text.strip()
        try:
            if self.row == 0:
                if not text:
                    raise ValueError("name cannot be empty")
                other = T.find(self.targets, text)
                if other is not None and other is not t:
                    raise ValueError(f"a target named {text} already exists")
                t.name = text
            elif self.row == 1:
                t.host = T.check_host(text)
            else:
                t.notes = text
        except ValueError as exc:
            self.message = (str(exc), "err")
            return
        if self._save():
            self.message = ("Saved.", "ok")

    # operation editor
    def _open_op(self, t: T.Target, index: int | None) -> None:
        self.op_index = index
        self.op = T.Op("ping", "", {}, "") if index is None else T.Op(t.ops[index].kind, t.ops[index].label,
                                                                       dict(t.ops[index].params), t.ops[index].every)
        self.form = self._op_form(self.op)
        self.mode = "op"

    def _op_form(self, op: T.Op) -> Form:
        fields = [Field("kind", "Operation", T.KIND_LABEL[op.kind], list(T.KIND_LABEL.values()), kind="choice"),
                  Field("label", "Your note", op.label, hint="what this check is for")]
        for key, label, _default, hint in T.OP_FIELDS[op.kind]:
            if key == "direction":
                fields.append(Field(key, label, op.value(key), ["down", "up"], kind="choice"))
            else:
                fields.append(Field(key, label, op.value(key), hint=hint))
        fields.append(Field("every", "Repeat every", op.every, hint="empty = only when you run it. e.g. 10m, 1h"))
        fields += [Field("save", "Save operation", kind="action"), Field("run", "Run once now", kind="action")]
        return Form(fields)

    def _collect_op(self) -> T.Op:
        assert self.form is not None and self.op is not None
        v = self.form.values()
        kind = next(k for k, lab in T.KIND_LABEL.items() if lab == v["kind"])
        params = {k: v[k] for k, *_ in T.OP_FIELDS[kind] if k in v}
        return T.Op(kind, v["label"].strip(), params, v["every"].strip())

    def _key_op(self, k: str) -> None:
        form = self.form
        assert form is not None
        if not form.editing:
            if k == "esc":
                self.mode, self.form, self.op = "detail", None, None
                return
            if k == "r":
                self._try_run_op()
                return
        event = form.key(k)
        if event == "changed:kind":
            kind = next(kd for kd, lab in T.KIND_LABEL.items() if lab == form.values()["kind"])
            keep = form.values()
            op = T.Op(kind, keep["label"], {}, keep["every"])
            self.op = op
            self.form = self._op_form(op)
        elif event == "action:save":
            self._save_op()
        elif event == "action:run":
            self._try_run_op()

    def _try_run_op(self) -> None:
        t = self.target
        if t is None:
            return
        op = self._collect_op()
        try:
            T.op_job(t, op)
        except ValueError as exc:
            self.message = (str(exc), "err")
            return
        self._run(t, [op])

    def _save_op(self) -> None:
        t = self.target
        if t is None:
            return
        op = self._collect_op()
        try:
            T.op_job(t, op)
        except ValueError as exc:
            self.message = (str(exc), "err")
            return
        if self.op_index is None:
            t.ops.append(op)
            self.row = 3 + len(t.ops) - 1
        else:
            t.ops[self.op_index] = op
        self.mode, self.form, self.op = "detail", None, None
        if self._save():
            sched = f" It repeats every {op.every} while the monitor runs." if op.every else ""
            self.message = ("Operation saved." + sched, "ok")

    # ---- drawing ----

    def render(self, w: int, h: int) -> list[Line]:
        self.hints = self.hint_text
        body = {"list": self._r_list, "new": self._r_new, "detail": self._r_detail, "op": self._r_op}[self.mode](w)
        if self.confirm:
            what = "this target" if self.confirm == "target" else "this operation"
            body += [L(), L(f" Delete {what}? Press y to confirm, any other key to cancel.", "warn")]
        if self.message:
            body += [L(), L(" " + self.message[0], self.message[1])]
        body += self._r_result()
        self._total, self._h = len(body), h
        return self.scroll.view(body, h)

    def _r_result(self) -> list[Line]:
        if self.task is not None:
            return [L(), L(f" {spinner(time.monotonic())} running for {time.monotonic() - self.started:.0f} s ...", "warn")]
        if not self.result:
            return []
        return [L(), L(" Result", "hdr")] + [L(" " + t, "err" if "FAILED" in t else "") for t in self.result]

    def _r_list(self, w: int) -> list[Line]:
        out: list[Line] = [L(" Saved addresses with your own operations. Run them by hand or on a schedule.", "dim"), L()]
        if not self.targets:
            return out + [
                L(" You have no targets yet. Press n to add one, for example your router,", "dim"),
                L(" a server you manage, or a game server you want to watch.", "dim"),
                L(),
                L(" Each target can hold several operations: a ping every 10 minutes, a TCP check", "dim"),
                L(" of port 8080, a UDP test to a Lacuna server, and so on, each with its own settings.", "dim"),
            ]
        out.append(L(f"   {'name':<24}{'address':<24}{'ops':>5}{'scheduled':>11}", "dim"))
        for i, t in enumerate(self.targets):
            here = i == self.cur
            row = f"{t.name[:23]:<24}{t.host[:23]:<24}{len(t.ops):>5}{t.scheduled():>11}"
            out.append([("> " if here else "  ", "key"), (" " + row, "sel" if here else "")])
        sel = self.target
        if sel and sel.notes:
            out += [L(), L(" Notes: " + sel.notes, "dim")]
        return out

    def _r_new(self, w: int) -> list[Line]:
        assert self.form is not None
        return [L(" New target", "hdr"), L()] + self.form.render(w)

    def _r_detail(self, w: int) -> list[Line]:
        t = self.target
        if t is None:
            return [L(" No target selected.", "dim")]
        out: list[Line] = [[(" Targets / ", "dim"), (t.name, "hdr")], L()]
        for i, (label, value) in enumerate((("Name", t.name), ("Address", t.host), ("Notes", t.notes))):
            here = self.row == i
            if here and self.edit is not None:
                val = self.edit.render(max(10, w - 16))
            else:
                val = [(value, "val" if here else "")]
            out.append([("> " if here else "  ", "key"), (f"{label:<9}", "hdr" if here else "")] + val)
        out += [L(), L(" Operations", "hdr")]
        for i, op in enumerate(t.ops):
            here = self.row == 3 + i
            when = f"every {op.every}" if op.every else "manual"
            out.append([("> " if here else "  ", "key"), (f"{i + 1:<3}{op.summary()}", "sel" if here else ""),
                        (f"   {when}", "ok" if op.every else "dim")])
        here = self.row == 3 + len(t.ops)
        out.append([("> " if here else "  ", "key"), ("+ Add operation", "sel" if here else "key")])
        return out

    def _r_op(self, w: int) -> list[Line]:
        t = self.target
        assert self.form is not None and t is not None
        title = "New operation" if self.op_index is None else f"Operation {self.op_index + 1}"
        return [[(f" {title} for ", "hdr"), (f"{t.name} ({t.host})", "val")], L()] + self.form.render(w)

