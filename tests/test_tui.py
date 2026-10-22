import contextlib
import io
import json
import time
import unittest

from support import PORT, Env

from lacuna import cli
from lacuna.db import Database
from lacuna.server import LacunaServer
from lacuna.tui import build_lines, sparkline
from lacuna.tui import settings as S
from lacuna.tui.app import MIN_H, MIN_W, App, normalize
from lacuna.tui.core import Editor, Field, Form, Scroller, Task, plain, side_by_side, wrap_block
from lacuna.tui.help_screen import TOPICS, render_topic
from lacuna.tui.probes import build_job
from lacuna.tui.services import Context  # noqa: F401


class CoreTests(unittest.TestCase):
    def test_editor(self):
        e = Editor("ab")
        for k in ("left", "x", "end", "y", "backspace", "backspace", "home", "delete"):
            self.assertIsNone(e.feed(k))
        self.assertEqual(e.text, "x")
        self.assertEqual(e.feed("enter"), "commit")
        self.assertEqual(Editor("q").feed("esc"), "cancel")
        self.assertIsNone(Editor("").feed("\x00"))  # control characters are ignored

    def test_editor_render_marks_cursor(self):
        segs = Editor("abc").render(20)
        self.assertEqual("".join(t for t, _ in segs), "abc ")
        self.assertEqual(segs[1][1], "sel")

    def test_form_events(self):
        form = Form([Field("a", "A", "x"), Field("c", "C", "one", ["one", "two"], kind="choice"),
                     Field("go", "Go", kind="action")])
        self.assertIsNone(form.key("enter"))
        self.assertTrue(form.editing)
        form.key("z")
        self.assertEqual(form.key("enter"), "changed:a")
        self.assertEqual(form.values()["a"], "xz")
        form.key("down")
        self.assertEqual(form.key("right"), "changed:c")
        self.assertEqual(form.values()["c"], "two")
        self.assertEqual(form.key("left"), "changed:c")
        form.key("down")
        self.assertEqual(form.key("enter"), "action:go")
        form.key("up")
        form.key("up")
        form.key("enter")
        form.key("esc")
        self.assertFalse(form.editing)

    def test_form_render(self):
        form = Form([Field("a", "Name", "val", hint="tip"), Field("go", "Run", kind="action")])
        rows = plain(form.render(60))
        self.assertIn("> Name", rows[0])
        self.assertIn("tip", rows[0])
        self.assertIn("[ Run ]", rows[1])

    def test_wrap_keeps_tables(self):
        text = "short\n" + "word " * 40 + "\n| a | b |\n  indented line"
        out = wrap_block(text, 30)
        self.assertTrue(all(len(x) <= 30 for x in out))
        self.assertIn("| a | b |", out)
        self.assertIn("  indented line", out)

    def test_scroller_clamps(self):
        sc = Scroller()
        lines = [[(str(i), "")] for i in range(50)]
        self.assertEqual(len(sc.view(lines, 10)), 10)
        sc.key("end", 50, 10)
        self.assertEqual(sc.top, 40)
        sc.key("down", 50, 10)
        self.assertEqual(sc.top, 40)
        sc.key("pgup", 50, 10)
        self.assertEqual(sc.top, 31)
        sc.key("home", 50, 10)
        self.assertEqual(sc.top, 0)
        self.assertFalse(sc.key("x", 50, 10))

    def test_side_by_side_pads(self):
        out = plain(side_by_side([[("ab", "")]], [[("1", "")], [("2", "")]], 5, 1))
        self.assertEqual(out, ["ab    1", "      2"])

    def test_task_captures_errors(self):
        ok = Task(lambda: 5)
        bad = Task(lambda: 1 / 0)
        end = time.time() + 3
        while not (ok.done and bad.done) and time.time() < end:
            time.sleep(0.01)
        self.assertEqual(ok.result, 5)
        self.assertIsInstance(bad.error, ZeroDivisionError)

    def test_normalize_strings(self):
        self.assertEqual(normalize("\n"), "enter")
        self.assertEqual(normalize("\t"), "tab")
        self.assertEqual(normalize("\x1b"), "esc")
        self.assertEqual(normalize("\x7f"), "backspace")
        self.assertEqual(normalize("\x03"), "ctrl-c")
        self.assertEqual(normalize("q"), "q")
        self.assertIsNone(normalize("\x15"))

    def test_sparkline(self):
        self.assertEqual(sparkline([], 5), "")
        self.assertEqual(sparkline([0, 0], 5), "▁▁")
        self.assertEqual(len(sparkline(list(range(100)), 20)), 20)
        self.assertEqual(sparkline([0, 10], 5), "▁█")


class SettingsTests(Env):
    def test_roundtrip_and_validation(self):
        s = S.Settings()
        S.apply(s, "profile", " lab ")
        S.apply(s, "refresh_s", "5")
        S.apply(s, "window", "7d")
        S.apply(s, "server_port", "9500")
        S.apply(s, "server_bind", "127.0.0.1")
        S.apply(s, "color", "off")
        back = S.load()
        self.assertEqual((back.profile, back.refresh_s, back.window, back.server_port), ("lab", 5.0, "7d", 9500))
        self.assertFalse(back.color)

    def test_rejects_bad_values_without_saving(self):
        s = S.Settings()
        for key, val in (("refresh_s", "0"), ("refresh_s", "abc"), ("window", "2y"), ("server_port", "70000"),
                         ("server_bind", "not-an-ip"), ("nope", "x")):
            with self.assertRaises(ValueError, msg=f"{key}={val}"):
                S.apply(s, key, val)
        self.assertFalse(S.settings_path().exists())

    def test_damaged_or_odd_files_give_defaults(self):
        p = S.settings_path()
        p.parent.mkdir(parents=True)
        for text in ("{not json", "[]", json.dumps({"refresh_s": "bad", "extra": 1, "window": "1h"})):
            p.write_text(text)
            s = S.load()
            self.assertEqual(s.refresh_s, 2.0)
        self.assertEqual(S.load().window, "1h")

    def test_effective_paths(self):
        s = S.Settings()
        self.assertTrue(str(s.effective_db()).startswith(str(self.tmp)))
        self.assertTrue(s.effective_profile())


class BuildJobTests(unittest.TestCase):
    def test_ok(self):
        job = build_job("ping", {"target": "1.1.1.1", "count": "20", "interval": "0.2"})
        self.assertEqual((job.kind, job.params["count"]), ("icmp", 20))
        job = build_job("udp", {"target": "h:9400", "count": "100", "pps": "50", "size": "300"})
        self.assertEqual(job.params["size"], 300)
        job = build_job("throughput", {"target": "h:9400", "secs": "5", "streams": "2", "direction": "up"})
        self.assertEqual(job.params["direction"], "up")
        self.assertEqual(build_job("http", {"target": "https://x/y", "secs": "5", "streams": "4"}).kind, "http")

    def test_errors(self):
        bad = [
            ("ping", {"target": "", "count": "1", "interval": "1"}),
            ("ping", {"target": "x", "count": "abc", "interval": "1"}),
            ("ping", {"target": "x", "count": "0", "interval": "1"}),
            ("ping", {"target": "x", "count": "5", "interval": "0.01"}),
            ("udp", {"target": "nohostport", "count": "5", "pps": "10", "size": "100"}),
            ("udp", {"target": "h:1", "count": "5", "pps": "500", "size": "100"}),
            ("udp", {"target": "h:1", "count": "5", "pps": "10", "size": "20"}),
            ("udp", {"target": "h:1", "count": "99999", "pps": "10", "size": "100"}),
            ("tcp", {"target": "h:1", "count": "5", "interval": "0.01"}),
            ("throughput", {"target": "h:1", "secs": "61", "streams": "1", "direction": "up"}),
            ("throughput", {"target": "h:1", "secs": "5", "streams": "9", "direction": "up"}),
        ]
        for kind, vals in bad:
            with self.assertRaises(ValueError, msg=f"{kind} {vals}"):
                build_job(kind, vals)

    def test_cap_message_points_at_the_cli(self):
        with self.assertRaises(ValueError) as cm:
            build_job("udp", {"target": "h:1", "count": "5", "pps": "500", "size": "100"})
        self.assertIn("hard cap", str(cm.exception))
        self.assertIn("CLI", str(cm.exception))


class AppTests(Env):
    def setUp(self):
        super().setUp()
        self.seed()
        self.app = App(self.ctx)

    def test_every_screen_renders_at_several_sizes(self):
        for w, h in ((64, 14), (80, 24), (100, 28), (140, 50)):
            for key in "12345678":
                self.app.handle(key)
                lines = self.app.compose(w, h)
                self.assertEqual(len(lines), h)
                for text in plain(lines):
                    self.assertLessEqual(len(text), w)

    def test_too_small(self):
        out = "\n".join(plain(self.app.compose(MIN_W - 1, MIN_H)))
        self.assertIn("too small", out)
        self.assertEqual(len(self.app.compose(20, 5)), 5)

    def test_navigation_keys(self):
        self.app.handle("4")
        self.assertEqual(self.app.screen.name, "monitor")
        self.app.handle("tab")
        self.assertEqual(self.app.screen.name, "server")
        self.app.handle("btab")
        self.app.handle("btab")
        self.assertEqual(self.app.screen.name, "targets")
        self.app.handle("?")
        self.assertEqual(self.app.screen.name, "help")
        self.app.handle("1")
        for _ in range(8):
            self.app.handle("tab")
        self.assertEqual(self.app.screen.name, "dashboard")
        self.app.handle("resize")
        self.assertFalse(self.app.quit)
        self.app.handle("q")
        self.assertTrue(self.app.quit)

    def test_arrows_brackets_and_mouse_switch_screens(self):
        names = [s.name for s in self.app.screens]
        self.app.handle("right")
        self.assertEqual(self.app.screen.name, names[1])
        self.app.handle("right")
        self.assertEqual(self.app.screen.name, names[2])
        self.app.handle("left")
        self.assertEqual(self.app.screen.name, names[1])
        self.app.handle("]")
        self.app.handle("[")
        self.assertEqual(self.app.screen.name, names[1])
        self.app.handle("1")
        self.app.handle("left")  # wraps around
        self.assertEqual(self.app.screen.name, names[-1])
        self.app.compose(120, 30)
        spans = self.app.tab_spans(120)
        self.assertEqual(len(spans), 8)
        start, end, index = spans[3]
        self.app.handle(f"mouse:{start + 1}:0")
        self.assertEqual(self.app.screen.name, names[index])
        self.app.handle(f"mouse:{start + 1}:5")  # a click below the tab bar does nothing
        self.assertEqual(self.app.screen.name, names[index])
        self.app.handle("mouse:0:0")  # the "lacuna" label is not a tab
        self.assertEqual(self.app.screen.name, names[index])

    def test_enter_changes_a_choice_and_arrows_still_switch_screens(self):
        self.app.handle("2")
        before = self.app.screen.form.fields[0].value
        self.app.handle("enter")
        self.assertEqual(self.app.screen.name, "probes")
        self.assertNotEqual(self.app.screen.form.fields[0].value, before)
        self.app.handle("right")
        self.assertEqual(self.app.screen.name, "targets")

    def test_typing_does_not_trigger_global_keys(self):
        self.app.handle("7")
        self.app.handle("enter")  # start editing the profile
        self.assertTrue(self.app.screen.captures_text)
        for k in "q1?":
            self.app.handle(k)
        self.assertFalse(self.app.quit)
        self.assertEqual(self.app.screen.name, "settings")
        self.app.handle("enter")
        self.assertEqual(self.settings.profile, "q1?")
        self.assertEqual(S.load().profile, "q1?")

    def test_ctrl_c_always_quits(self):
        self.app.handle("7")
        self.app.handle("enter")
        self.app.handle("ctrl-c")
        self.assertTrue(self.app.quit)

    def test_tab_bar_marks_running_services(self):
        self.ctx.server.start()
        bar = "".join(t for t, _ in self.app.tab_bar(120))
        self.assertIn("Server *", bar)
        self.assertNotIn("Monitor *", bar)


class DashboardTests(Env):
    def test_empty_state_guides_the_user(self):
        app = App(self.ctx)
        text = "\n".join(plain(app.compose(100, 28)))
        self.assertIn("Nothing measured", text)
        self.assertIn("Offline demo", text)

    def test_with_data(self):
        self.seed()
        app = App(self.ctx)
        text = "\n".join(plain(app.compose(110, 30)))
        self.assertIn("Profile wifi", text)
        self.assertIn("2.00 %", text)
        self.assertIn("42.0 Mbit/s", text)
        self.assertIn("1.1.1.1", text)

    def test_profile_and_window_keys(self):
        self.seed("a")
        self.seed("b")
        app = App(self.ctx)
        dash = app.screen
        first = dash.profile
        app.handle("p")
        self.assertNotEqual(dash.profile, first)
        app.handle("P")
        self.assertEqual(dash.profile, first)
        seen = []
        for _ in range(5):
            app.handle("w")
            seen.append(dash.window)
        self.assertEqual(seen[-1], S.Settings().window)
        self.assertEqual(len(set(seen)), 5)

    def test_demo_toggle_needs_demo_data(self):
        app = App(self.ctx)
        app.handle("d")
        self.assertFalse(self.ctx.use_demo)
        self.assertIn("No demo data", self.ctx.status[0])

    def test_unreadable_db_shows_error(self):
        self.ctx.db_override = self.tmp
        app = App(self.ctx)
        self.assertIn("cannot read", "\n".join(plain(app.compose(100, 28))))

    def test_plain_snapshot(self):
        self.seed()
        with Database(self.settings.db_path) as db:
            text = "\n".join(build_lines(db, None, 100))
        self.assertIn("profile: wifi", text)
        self.assertIn("1.1.1.1", text)
        with Database(":memory:") as db:
            self.assertIn("No data yet", build_lines(db, None)[0])


class ProbesScreenTests(Env):
    def setUp(self):
        super().setUp()
        self.app = App(self.ctx)
        self.app.handle("2")
        self.screen = self.app.screen

    def select(self, label):
        for _ in range(8):
            if self.screen.form.fields[0].value == label:
                return
            self.screen.key("enter")
        self.fail("probe not found")

    def test_validation_error_is_shown_not_run(self):
        self.select("UDP probe")
        self.screen.key("r")  # target is empty
        self.assertIsNone(self.screen.task)
        self.assertEqual(self.screen.result_style, "err")
        self.assertIn("target", self.screen.result[0])

    def test_udp_run_against_the_in_app_server(self):
        self.ctx.server.start()
        self.app.handle("2")  # on_show fills in the local server address
        self.select("UDP probe")
        self.assertEqual(self.screen.form.values()["target"], f"127.0.0.1:{PORT}")
        self.screen.form.set("count", "60")
        self.screen.form.set("pps", "150")
        self.screen.key("r")
        self.assertIsNotNone(self.screen.task)
        self.screen.key("r")  # second press while running is refused
        self.assertIn("already running", self.ctx.status[0])
        self.wait(self.screen)
        text = "\n".join(self.screen.result)
        self.assertIn("uplink 0.00 %", text)
        self.assertIn("60 sent", text)
        with Database(self.settings.db_path) as db:
            self.assertEqual(db.counts()["loss_samples"], 1)
        self.assertIn("Result", "\n".join(plain(self.screen.render(100, 30))))

    def test_throughput_run(self):
        self.ctx.server.start()
        self.app.handle("2")
        self.select("Throughput (Lacuna server)")
        self.screen.form.set("secs", "0.4")
        self.screen.form.set("direction", "up")
        self.screen.key("r")
        self.wait(self.screen)
        self.assertIn("Mbit/s", "\n".join(self.screen.result))

    def test_failed_probe_shows_a_problem(self):
        self.select("UDP probe")
        self.screen.form.set("target", "127.0.0.1:9479")
        self.screen.form.set("count", "5")
        self.screen.key("r")
        self.wait(self.screen, 20)
        self.assertEqual(self.screen.result_style, "err")
        self.assertIn("HELLO_ACK", self.screen.result[0])

    def test_values_survive_switching_probes(self):
        self.screen.form.set("target", "9.9.9.9")
        self.select("TCP connect")
        self.select("Ping (ICMP)")
        self.assertEqual(self.screen.form.values()["target"], "9.9.9.9")

    def test_probe_results_never_go_to_the_demo_database(self):
        self.ctx.use_demo = True
        self.assertEqual(self.ctx.db_path_real(), self.settings.effective_db())
        self.assertNotEqual(self.ctx.db_path(), self.ctx.db_path_real())


class ServicesTests(Env):
    def test_server_screen(self):
        self.app = App(self.ctx)
        self.app.handle("5")
        self.app.handle("s")
        self.assertTrue(self.ctx.server.running)
        self.assertIn("LISTENING", "\n".join(plain(self.app.compose(100, 28))))
        self.app.handle("s")
        self.assertFalse(self.ctx.server.running)

    def test_server_port_in_use_is_reported(self):
        blocker = LacunaServer("127.0.0.1", PORT).start()
        self.addCleanup(blocker.stop)
        app = App(self.ctx)
        app.handle("5")
        app.handle("s")
        self.assertFalse(self.ctx.server.running)
        self.assertIn("Could not start", self.ctx.status[0])

    def test_monitor_start_stop_and_store(self):
        srv = LacunaServer("127.0.0.1", PORT + 2).start()
        self.addCleanup(srv.stop)
        cfg = self.tmp / "m.toml"
        cfg.write_text(f'[[job]]\nkind = "udp"\ntarget = "127.0.0.1:{PORT + 2}"\nevery = "30s"\n'
                       "count = 30\npps = 150\n")
        self.settings.config_path = str(cfg)
        app = App(self.ctx)
        app.handle("4")
        app.handle("s")
        self.assertTrue(self.ctx.monitor.running)
        end = time.time() + 15
        while self.ctx.monitor.runs < 1 and time.time() < end:
            time.sleep(0.1)
        self.assertGreaterEqual(self.ctx.monitor.runs, 1)
        text = "\n".join(plain(app.compose(110, 30)))
        self.assertIn("RUNNING", text)
        self.assertIn("udp 127.0.0.1", text)
        app.handle("s")
        end = time.time() + 5
        while self.ctx.monitor.running and time.time() < end:
            time.sleep(0.05)
        self.assertFalse(self.ctx.monitor.running)
        with Database(self.settings.db_path) as db:
            self.assertGreaterEqual(db.counts()["loss_samples"], 1)

    def test_monitor_bad_config_is_reported(self):
        bad = self.tmp / "bad.toml"
        bad.write_text('[[job]]\nkind = "nope"\ntarget = "x"\n')
        self.settings.config_path = str(bad)
        app = App(self.ctx)
        app.handle("4")
        app.handle("s")
        self.assertFalse(self.ctx.monitor.running)
        self.assertIn("kind must be", "\n".join(plain(app.compose(100, 28))))


class ReportAndSettingsScreenTests(Env):
    def test_report_screen_writes_files(self):
        self.seed("a")
        self.seed("b")
        app = App(self.ctx)
        app.handle("6")
        scr = app.screen
        self.assertEqual(scr.chosen, {"a", "b"})
        app.handle(" ")
        self.assertEqual(len(scr.chosen), 1)
        app.handle("a")
        self.assertEqual(len(scr.chosen), 2)
        for key, name in (("r", "lacuna-report.html"), ("c", "lacuna-export.csv"), ("j", "lacuna-export.json")):
            app.handle(key)
            self.assertTrue((self.tmp / "out" / name).exists(), name)
        self.assertIn("Lacuna report", (self.tmp / "out" / "lacuna-report.html").read_text())
        json.loads((self.tmp / "out" / "lacuna-export.json").read_text())

    def test_report_needs_a_profile_and_a_report_to_open(self):
        app = App(self.ctx)
        app.handle("6")
        app.handle("r")
        self.assertIn("Select at least one", app.screen.message[0])
        app.handle("o")
        self.assertIn("Build a report first", app.screen.message[0])

    def test_settings_screen_edits_and_rejects(self):
        app = App(self.ctx)
        app.handle("7")
        scr = app.screen
        keys = ["down"] * 3  # refresh row
        for k in keys:
            app.handle(k)
        app.handle("enter")
        for _ in range(3):
            app.handle("backspace")
        for ch in "9":
            app.handle(ch)
        app.handle("enter")
        self.assertEqual(self.settings.refresh_s, 9.0)
        app.handle("enter")
        for _ in range(3):
            app.handle("backspace")
        app.handle("0")
        app.handle("enter")
        self.assertEqual(self.settings.refresh_s, 9.0)  # 0 was rejected
        self.assertEqual(scr.message[1], "err")

    def test_settings_reset(self):
        S.apply(self.settings, "profile", "zzz")
        app = App(self.ctx)
        app.handle("7")
        for _ in range(9):
            app.handle("down")
        app.handle("enter")
        self.assertEqual(self.ctx.settings.profile, "")
        self.assertEqual(S.load().profile, "")


class HelpTests(Env):
    def test_every_topic_renders_at_every_width(self):
        for w in (30, 50, 80, 120):
            for _, text in TOPICS:
                for ln in plain(render_topic(text, w)):
                    self.assertLessEqual(len(ln), w)

    def test_topics_have_content_and_no_em_dash(self):
        self.assertGreaterEqual(len(TOPICS), 8)
        for name, text in TOPICS:
            self.assertTrue(name)
            self.assertGreater(len(text), 80, name)
            self.assertNotIn(chr(0x2014), text)

    def test_navigation_and_scroll(self):
        app = App(Context(S.Settings()))
        app.handle("8")
        h = app.screen
        app.handle("down")
        self.assertEqual(h.topic, 1)
        for _ in range(30):
            app.handle("down")
        self.assertEqual(h.topic, len(TOPICS) - 1)
        for _ in range(30):
            app.handle("up")
        self.assertEqual(h.topic, 0)
        app.compose(80, 14)
        app.handle("pgdn")
        self.assertGreaterEqual(h.scroll.top, 0)
        app.handle("1")


class EntryPointTests(Env):
    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_bare_command_without_a_terminal_prints_help(self):
        code, out = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("COMMAND", out)

    def test_tui_command_without_a_terminal_prints_a_snapshot(self):
        self.seed()
        code, out = self.run_cli("tui", "--db", self.settings.db_path)
        self.assertEqual(code, 0)
        self.assertIn("profile: wifi", out)


if __name__ == "__main__":
    unittest.main()
