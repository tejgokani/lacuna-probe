import contextlib
import io
import json
import threading
import time
import unittest

from support import Env

from lacuna import cli, config
from lacuna import targets as T
from lacuna.db import Database
from lacuna.monitor import Monitor
from lacuna.server import LacunaServer
from lacuna.tui.app import App
from lacuna.tui.core import plain

PORT = 9480


def keys(app, *ks):
    for k in ks:
        app.handle(k)


def typed(app, text):
    for ch in text:
        app.handle(ch)


class TargetModelTests(Env):
    def test_op_jobs_per_kind(self):
        t = T.Target("gw", "10.0.0.1")
        job = T.op_job(t, T.Op("ping", params={"count": "50"}))
        self.assertEqual((job.kind, job.target, job.params["count"]), ("icmp", "10.0.0.1", 50))
        job = T.op_job(t, T.Op("tcp", params={"port": "8080", "count": "3"}))
        self.assertEqual((job.kind, job.target), ("tcp", "10.0.0.1:8080"))
        job = T.op_job(t, T.Op("udp", params={"port": "9400"}, every="10m"))
        self.assertEqual((job.target, job.every_s), ("10.0.0.1:9400", 600))
        job = T.op_job(t, T.Op("throughput", params={"direction": "up", "secs": "5"}))
        self.assertEqual(job.params["direction"], "up")
        job = T.op_job(t, T.Op("http", params={"url": "http://{host}/status"}))
        self.assertEqual(job.target, "http://10.0.0.1/status")

    def test_validation(self):
        t = T.Target("gw", "10.0.0.1")
        bad_ops = [
            T.Op("ping", params={"interval": "0.01"}),  # over the 20 pps cap
            T.Op("udp", params={"pps": "500"}),
            T.Op("tcp", params={"port": "abc"}),
            T.Op("tcp", params={"port": "99999"}),
            T.Op("http", params={"url": "ftp://x"}),
            T.Op("throughput", params={"secs": "120"}),
            T.Op("ping", every="1s"),  # schedule too fast
            T.Op("ping", every="soon"),
        ]
        for op in bad_ops:
            with self.assertRaises(ValueError, msg=str(op)):
                T.op_job(t, op)
        for host in ("", "a b", "bad;rm -rf", "-x", "x-", "a" * 300, "http://x"):
            with self.assertRaises(ValueError, msg=host):
                T.check_host(host)
        self.assertEqual(T.check_host(" 10.0.0.1 "), "10.0.0.1")
        self.assertEqual(T.check_host("my-host.example.org"), "my-host.example.org")

    def test_host_cannot_inject_into_the_ping_command(self):
        with self.assertRaises(ValueError):
            T.op_job(T.Target("x", "1.1.1.1; reboot"), T.Op("ping"))

    def test_roundtrip_and_damaged_files(self):
        targets = [T.Target("gw", "10.0.0.1", "notes here",
                            [T.Op("ping", "base check", {"count": "5"}, "5m"), T.Op("tcp", "", {"port": "22"})])]
        T.save(targets)
        back = T.load()
        self.assertEqual(back[0].name, "gw")
        self.assertEqual(back[0].ops[0].params, {"count": "5"})
        self.assertEqual((back[0].ops[0].every, back[0].scheduled()), ("5m", 1))
        p = T.targets_path()
        for text in ("{bad", "[]", json.dumps({"targets": [1, {"name": "x"}, {"name": "ok", "host": "h", "ops": "no"}]})):
            p.write_text(text)
            loaded = T.load()
            self.assertLessEqual(len(loaded), 1)
        self.assertEqual(T.load()[0].name, "ok")

    def test_unknown_operation_kinds_are_dropped(self):
        p = T.targets_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"targets": [{"name": "a", "host": "h",
                                              "ops": [{"kind": "rm -rf"}, {"kind": "ping", "params": {"count": 3}}]}]}))
        ops = T.load()[0].ops
        self.assertEqual([o.kind for o in ops], ["ping"])
        self.assertEqual(ops[0].params, {"count": "3"})

    def test_scheduled_jobs_skip_invalid_and_manual(self):
        t = T.Target("gw", "10.0.0.1", ops=[T.Op("ping", every="10m"), T.Op("ping"),
                                            T.Op("udp", params={"pps": "999"}, every="10m")])
        jobs, errors = T.scheduled_jobs([t])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("gw operation 3", errors[0])

    def test_find_ignores_case(self):
        self.assertEqual(T.find([T.Target("Office", "h")], "office").name, "Office")
        self.assertIsNone(T.find([], "x"))

    def test_summary(self):
        self.assertEqual(T.Op("ping", "base").summary(), "base: Ping (ICMP), count 20, interval 0.2")
        self.assertTrue(T.Op("tcp").summary().startswith("TCP connect, port 443"))


class TargetsScreenTests(Env):
    def setUp(self):
        super().setUp()
        self.app = App(self.ctx)
        self.app.handle("3")
        self.screen = self.app.screen

    def create(self, name="office", host="127.0.0.1"):
        keys(self.app, "n", "enter")
        typed(self.app, name)
        keys(self.app, "enter", "down", "enter")
        typed(self.app, host)
        keys(self.app, "enter", "down", "down", "enter")  # Create target

    def test_empty_state_explains_itself(self):
        text = "\n".join(plain(self.app.compose(100, 28)))
        self.assertIn("no targets yet", text)

    def test_create_edit_and_persist(self):
        self.create()
        self.assertEqual(self.screen.mode, "detail")
        self.assertEqual([t.name for t in T.load()], ["office"])
        self.assertEqual(T.load()[0].ops[0].kind, "ping")  # starter operation
        # rename
        self.app.handle("up")
        self.app.handle("up")
        self.app.handle("up")
        self.app.handle("enter")
        for _ in range(10):
            self.app.handle("backspace")
        typed(self.app, "lab")
        self.app.handle("enter")
        self.assertEqual(T.load()[0].name, "lab")
        text = "\n".join(plain(self.app.compose(100, 28)))
        self.assertIn("Targets / lab", text)
        self.assertIn("Operations", text)

    def test_create_rejects_bad_input(self):
        keys(self.app, "n", "down", "down", "down", "enter")
        self.assertEqual(self.screen.message[1], "err")  # no name
        self.assertEqual(T.load(), [])
        keys(self.app, "up", "up", "up", "enter")
        typed(self.app, "x")
        keys(self.app, "enter", "down", "enter")
        typed(self.app, "bad host!")
        keys(self.app, "enter", "down", "down", "enter")
        self.assertIn("address", self.screen.message[0])
        self.assertEqual(T.load(), [])
        keys(self.app, "esc")
        self.assertEqual(self.screen.mode, "list")

    def test_duplicate_names_refused(self):
        self.create("a")
        keys(self.app, "esc")
        self.create("A")
        self.assertIn("already exists", self.screen.message[0])
        self.assertEqual(len(T.load()), 1)

    def test_add_edit_and_delete_operations(self):
        self.create()
        # add: move to "+ Add operation", open it
        keys(self.app, "down", "down")  # row 3 is the starter op, 4 is "+ Add operation"
        while self.screen.row < 4:
            self.app.handle("down")
        self.app.handle("enter")
        self.assertEqual(self.screen.mode, "op")
        self.app.handle("enter")  # choose the next kind: TCP connect
        self.assertEqual(self.screen.form.values()["kind"], "TCP connect")
        self.assertIn("port", self.screen.form.values())
        form = self.screen.form
        form.set("port", "8080")
        form.set("count", "3")
        form.set("every", "15m")
        form.set("label", "web port")
        while form.fields[form.cursor].key != "save":
            self.app.handle("down")
        self.app.handle("enter")
        self.assertEqual(self.screen.mode, "detail")
        ops = T.load()[0].ops
        self.assertEqual([o.kind for o in ops], ["ping", "tcp"])
        self.assertEqual((ops[1].params["port"], ops[1].every, ops[1].label), ("8080", "15m", "web port"))
        self.assertIn("every 15m", "\n".join(plain(self.app.compose(110, 28))))
        # delete it again, with confirmation
        self.app.handle("x")
        self.assertIsNotNone(self.screen.confirm)
        self.app.handle("n")
        self.assertEqual(len(T.load()[0].ops), 2)
        self.app.handle("x")
        self.app.handle("y")
        self.assertEqual(len(T.load()[0].ops), 1)

    def test_invalid_operation_is_not_saved(self):
        self.create()
        while self.screen.row < 4:
            self.app.handle("down")
        self.app.handle("enter")
        form = self.screen.form
        form.set("interval", "0.001")
        while form.fields[form.cursor].key != "save":
            self.app.handle("down")
        self.app.handle("enter")
        self.assertEqual(self.screen.mode, "op")
        self.assertIn("hard cap", self.screen.message[0])
        self.assertEqual(len(T.load()[0].ops), 1)
        self.app.handle("esc")
        self.assertEqual(self.screen.mode, "detail")

    def test_run_an_operation_against_the_in_app_server(self):
        self.ctx.server.start()
        self.create()
        t = T.load()[0]
        t.ops = [T.Op("udp", "local echo", {"port": str(PORT - 480 + 470), "count": "40", "pps": "150"})]
        T.save([t])
        self.app.handle("3")
        keys(self.app, "enter")  # open the target
        keys(self.app, "down", "down", "down")
        self.app.handle("r")
        self.assertIsNotNone(self.screen.task)
        end = time.time() + 20
        while self.screen.task is not None and time.time() < end:
            self.app.tick()
            time.sleep(0.05)
        text = "\n".join(self.screen.result)
        self.assertIn("40 sent, 40 received", text)
        self.assertIn("uplink 0.00 %", text)
        with Database(self.settings.db_path) as db:
            self.assertEqual(db.counts()["loss_samples"], 1)

    def test_failed_operation_reports_and_continues(self):
        self.create()
        t = T.load()[0]
        t.ops = [T.Op("udp", "dead", {"port": "9489", "count": "5"}), T.Op("tcp", "closed", {"port": "9488", "count": "1"})]
        T.save([t])
        self.app.handle("3")
        self.app.handle("R")  # run all operations of this target
        end = time.time() + 30
        while self.screen.task is not None and time.time() < end:
            self.app.tick()
            time.sleep(0.05)
        text = "\n".join(self.screen.result)
        self.assertIn("FAILED", text)
        self.assertIn("[2]", text)  # the second operation still ran

    def test_delete_target(self):
        self.create()
        keys(self.app, "esc", "d")
        self.assertIsNotNone(self.screen.confirm)
        self.app.handle("y")
        self.assertEqual(T.load(), [])

    def test_typing_in_a_field_is_not_a_global_key(self):
        keys(self.app, "n", "enter")
        typed(self.app, "q1?")
        self.assertFalse(self.app.quit)
        self.assertEqual(self.app.screen.name, "targets")

    def test_renders_at_small_size(self):
        self.create()
        for key in ("enter", "esc"):
            for w, h in ((64, 14), (100, 28)):
                for line in plain(self.app.compose(w, h)):
                    self.assertLessEqual(len(line), w)
            self.app.handle(key)


class MonitorIntegrationTests(Env):
    def test_scheduled_target_operations_join_the_monitor(self):
        srv = LacunaServer("127.0.0.1", PORT + 2).start()
        self.addCleanup(srv.stop)
        T.save([T.Target("local", "127.0.0.1", ops=[
            T.Op("udp", "echo", {"port": str(PORT + 2), "count": "30", "pps": "150"}, "30s"),
            T.Op("ping", "manual only")])])
        cfg_file = self.tmp / "m.toml"
        cfg_file.write_text('[[job]]\nkind = "tcp"\ntarget = "127.0.0.1:9"\nevery = "1h"\ncount = 1\n')
        self.settings.config_path = str(cfg_file)
        cfg = self.ctx.monitor.load_config()
        self.assertEqual([j.kind for j in cfg.jobs], ["tcp", "udp"])
        self.assertEqual(self.ctx.monitor.target_jobs, 1)
        stop = threading.Event()
        with Database(cfg.db_path) as db:
            Monitor(config.Config("m", cfg.db_path, cfg.jobs[1:]), db, stop, lambda m: None).run(max_runs=1)
            self.assertEqual(db.counts()["loss_samples"], 1)

    def test_invalid_scheduled_operation_is_reported_not_fatal(self):
        T.save([T.Target("x", "127.0.0.1", ops=[T.Op("udp", params={"pps": "999"}, every="5m")])])
        self.ctx.monitor.load_config()
        self.assertEqual(self.ctx.monitor.target_jobs, 0)
        self.assertIn("x operation 1", self.ctx.monitor.target_problems[0])


class CliTargetsTests(Env):
    def run_cli(self, *argv):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_targets_listing(self):
        code, out, _ = self.run_cli("targets")
        self.assertIn("no saved targets", out)
        T.save([T.Target("gw", "10.0.0.1", "main router", [T.Op("ping", "", {}, "10m"), T.Op("tcp", "ssh", {"port": "22"})])])
        code, out, _ = self.run_cli("targets")
        self.assertEqual(code, 0)
        self.assertIn("gw  10.0.0.1   (main router)", out)
        self.assertIn("[every 10m]", out)
        self.assertIn("[manual]", out)

    def test_run_target(self):
        srv = LacunaServer("127.0.0.1", PORT + 4).start()
        self.addCleanup(srv.stop)
        T.save([T.Target("Local", "127.0.0.1", ops=[
            T.Op("udp", "echo", {"port": str(PORT + 4), "count": "30", "pps": "150"}),
            T.Op("tcp", "tcp", {"port": str(PORT + 5), "count": "2", "interval": "0.2"})])])
        db = str(self.tmp / "cli.db")
        code, out, _ = self.run_cli("run", "local", "--op", "1", "--db", db, "--profile", "p")
        self.assertEqual(code, 0, out)
        self.assertIn("30 sent, 30 received", out)
        with Database(db) as d:
            self.assertEqual(d.counts()["loss_samples"], 1)
        code, out, _ = self.run_cli("run", "Local", "--db", db, "--json")
        self.assertEqual(out.count('"kind"'), 2)

    def test_run_errors(self):
        T.save([T.Target("gw", "10.0.0.1", ops=[T.Op("ping")]), T.Target("empty", "10.0.0.2")])
        for argv in (("run", "nope"), ("run", "gw", "--op", "5"), ("run", "empty")):
            code, _, err = self.run_cli(*argv, "--no-store")
            self.assertEqual(code, 2, argv)
            self.assertIn("lacuna: error", err)


if __name__ == "__main__":
    unittest.main()
