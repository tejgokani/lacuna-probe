import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from lacuna import cli, config
from lacuna.db import Database
from lacuna.monitor import Monitor
from lacuna.server import LacunaServer

BASE = 9460


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class IsolatedEnv(unittest.TestCase):
    """Point XDG dirs at a temp folder so tests never touch the real database."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self._saved = {k: os.environ.get(k) for k in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "LACUNA_DB")}
        os.environ["XDG_DATA_HOME"] = str(self.tmp / "data")
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "conf")
        os.environ.pop("LACUNA_DB", None)
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class RateCapTests(unittest.TestCase):
    def test_within_cap_is_silent(self):
        self.assertIsNone(config.check_rate("udp", 200))
        self.assertIsNone(config.check_rate("icmp", 5))

    def test_over_cap_refused(self):
        for kind, v in (("icmp", 21), ("udp", 201), ("tcp", 11), ("throughput", 61)):
            with self.assertRaises(config.ConfigError):
                config.check_rate(kind, v)

    def test_unsafe_rate_warns(self):
        self.assertIn("warning", config.check_rate("udp", 500, unsafe=True))

    def test_cli_enforces_caps(self):
        code, _, err = run_cli("udp", "127.0.0.1:1", "--pps", "500", "--no-store")
        self.assertEqual(code, 2)
        self.assertIn("hard cap", err)
        code, _, err = run_cli("ping", "127.0.0.1", "--interval", "0.01", "--no-store")
        self.assertEqual(code, 2)
        code, _, err = run_cli("throughput", "127.0.0.1:1", "--secs", "120", "--no-store")
        self.assertEqual(code, 2)
        code, _, err = run_cli("tcp", "127.0.0.1:1", "--interval", "0.01", "--no-store")
        self.assertEqual(code, 2)


class ConfigTests(IsolatedEnv):
    def write(self, text):
        p = self.tmp / "c.toml"
        p.write_text(text)
        return p

    def test_defaults(self):
        cfg = config.load_config(None, "x")
        self.assertEqual(cfg.profile, "x")
        kinds = [j.kind for j in cfg.jobs]
        self.assertEqual(kinds.count("icmp"), 3)
        self.assertEqual(kinds.count("tcp"), 2)
        self.assertEqual(kinds.count("http"), 1)
        self.assertIn("speed.cloudflare.com", [j.target for j in cfg.jobs if j.kind == "http"][0])
        self.assertTrue(str(cfg.db_path).startswith(str(self.tmp)))

    def test_file(self):
        p = self.write('profile = "wifi"\n[[job]]\nkind = "icmp"\ntarget = "1.1.1.1"\nevery = "2m"\ncount = 5\n')
        cfg = config.load_config(p)
        self.assertEqual(cfg.profile, "wifi")
        self.assertEqual(cfg.jobs[0].every_s, 120)
        self.assertEqual(cfg.jobs[0].params["count"], 5)

    def test_profile_override(self):
        p = self.write('profile = "wifi"\n[[job]]\nkind = "tcp"\ntarget = "h:1"\n')
        self.assertEqual(config.load_config(p, "other").profile, "other")

    def test_validation(self):
        bad = [
            '[[job]]\nkind = "nope"\ntarget = "x"\n',
            '[[job]]\nkind = "icmp"\n',
            '[[job]]\nkind = "udp"\ntarget = "nohostport"\n',
            '[[job]]\nkind = "udp"\ntarget = "h:1"\npps = 999\n',
            '[[job]]\nkind = "icmp"\ntarget = "x"\nevery = "banana"\n',
            "this is not toml [",
        ]
        for text in bad:
            with self.assertRaises(config.ConfigError, msg=text):
                config.load_config(self.write(text))

    def test_missing_file(self):
        with self.assertRaises(config.ConfigError):
            config.load_config(self.tmp / "missing.toml")

    def test_durations(self):
        self.assertEqual(config.parse_duration("90"), 90)
        self.assertEqual(config.parse_duration("15m"), 900)
        self.assertEqual(config.parse_duration("2h"), 7200)
        self.assertEqual(config.parse_duration(30), 30)

    def test_example_config_is_valid(self):
        example = Path(__file__).resolve().parent.parent / "examples" / "lacuna.toml"
        if example.exists():
            self.assertTrue(config.load_config(example).jobs)


class EndToEndTests(IsolatedEnv):
    def setUp(self):
        super().setUp()
        self.srv = LacunaServer("127.0.0.1", BASE).start()
        self.addCleanup(self.srv.stop)
        self.db = str(self.tmp / "e2e.db")

    def test_udp_json_and_store(self):
        code, out, _ = run_cli("udp", f"127.0.0.1:{BASE}", "--count", "60", "--pps", "150",
                               "--profile", "lo", "--db", self.db, "--json")
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual(doc["kind"], "udp")
        self.assertEqual(doc["sample"]["sent"], 60)
        self.assertEqual(doc["sample"]["recv"], 60)
        with Database(self.db) as db:
            self.assertEqual(db.counts()["loss_samples"], 1)
            self.assertEqual(db.profiles(), ["lo"])

    def test_no_store_skips_db(self):
        code, out, _ = run_cli("udp", f"127.0.0.1:{BASE}", "--count", "20", "--pps", "100",
                               "--no-store", "--db", self.db)
        self.assertEqual(code, 0)
        self.assertIn("uplink 0.00 %", out)
        self.assertFalse(Path(self.db).exists())

    def test_tcp_and_throughput_then_report_compare_export(self):
        for prof in ("a", "b"):
            self.assertEqual(run_cli("udp", f"127.0.0.1:{BASE}", "--count", "60", "--pps", "150",
                                     "--profile", prof, "--db", self.db)[0], 0)
            self.assertEqual(run_cli("tcp", f"127.0.0.1:{BASE + 1}", "--count", "3", "--interval", "0.2",
                                     "--profile", prof, "--db", self.db)[0], 0)
            code, out, _ = run_cli("throughput", f"127.0.0.1:{BASE}", "--secs", "0.4", "--direction", "up",
                                   "--profile", prof, "--db", self.db)
            self.assertEqual(code, 0, out)
            self.assertIn("Mbit/s", out)
        report = self.tmp / "r.html"
        code, out, _ = run_cli("report", "--db", self.db, "-o", str(report))
        self.assertEqual(code, 0)
        self.assertIn('id="compare"', report.read_text())
        code, out, _ = run_cli("compare", "a", "b", "--db", self.db)
        self.assertEqual(code, 0)
        self.assertIn("mean loss %", out)
        code, out, _ = run_cli("export", "--format", "json", "--db", self.db)
        doc = json.loads(out)
        self.assertEqual(len(doc["loss_samples"]), 4)
        self.assertEqual(len(doc["tput_samples"]), 2)
        code, out, _ = run_cli("export", "--db", self.db, "--profile", "a", "--since", "1d")
        self.assertEqual(out.splitlines()[0].split(",")[0], "sample")

    def test_compare_unknown_profile(self):
        run_cli("udp", f"127.0.0.1:{BASE}", "--count", "20", "--pps", "100", "--profile", "a", "--db", self.db)
        code, _, err = run_cli("compare", "a", "zzz", "--db", self.db)
        self.assertEqual(code, 2)
        self.assertIn("zzz", err)

    def test_report_on_empty_db_is_an_error(self):
        code, _, err = run_cli("report", "--db", self.db, "-o", str(self.tmp / "x.html"))
        self.assertEqual(code, 2)
        self.assertIn("no data", err)

    def test_dead_target_is_a_clean_failure(self):
        code, _, err = run_cli("udp", "127.0.0.1:9469", "--count", "5", "--no-store")
        self.assertEqual(code, 1)
        self.assertIn("HELLO_ACK", err)


class MonitorTests(IsolatedEnv):
    def test_runs_jobs_and_survives_failures(self):
        srv = LacunaServer("127.0.0.1", BASE + 20).start()
        self.addCleanup(srv.stop)
        jobs = [
            config.Job("udp", f"127.0.0.1:{BASE + 20}", 5, {"count": 30, "pps": 150}),
            config.Job("tcp", f"127.0.0.1:{BASE + 21}", 5, {"count": 2, "interval": 0.2}),
            config.Job("icmp", "no-such-host.invalid", 5, {"count": 1, "interval": 1}),
        ]
        cfg = config.Config("m", self.tmp / "m.db", jobs)
        lines = []
        with Database(cfg.db_path) as db:
            Monitor(cfg, db, threading.Event(), lines.append).run(max_runs=3)
            self.assertEqual(db.counts()["loss_samples"], 2)
        self.assertEqual(len(lines), 3)
        self.assertTrue(any("FAILED" in ln for ln in lines))

    def test_stop_event_exits_promptly(self):
        cfg = config.Config("m", self.tmp / "m.db", [config.Job("icmp", "127.0.0.1", 60, {"count": 1})])
        stop = threading.Event()
        stop.set()
        with Database(cfg.db_path) as db:
            Monitor(cfg, db, stop, lambda m: None).run()


class DoctorAndTuiTests(IsolatedEnv):
    def test_doctor_passes(self):
        code, out, _ = run_cli("doctor")
        self.assertEqual(code, 0, out)
        self.assertIn("[OK  ] database", out)

    def test_tui_lines(self):
        from lacuna.tui import build_lines, sparkline

        with Database(":memory:") as db:
            self.assertIn("No data yet", build_lines(db, None)[0])
            from lacuna.db import LossSample

            rid = db.add_run("p", "icmp", "1.1.1.1", ts=1000.0)
            db.add_loss(rid, LossSample(10, 9, 10.0, rtt_p50=11.0, rtt_p95=15.0, jitter_ms=0.5))
            text = "\n".join(build_lines(db, None, 100, now=1060.0))
            self.assertIn("1.1.1.1", text)
            self.assertIn("profile: p", text)
        self.assertEqual(sparkline([0, 0, 0], 10), "▁" * 3)
        self.assertEqual(len(sparkline(list(range(100)), 20)), 20)

    def test_help_and_version(self):
        with self.assertRaises(SystemExit) as cm:
            run_cli("--version")
        self.assertEqual(cm.exception.code, 0)
        code, out, _ = run_cli()
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
