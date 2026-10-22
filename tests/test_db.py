import os
import tempfile
import unittest
from pathlib import Path

from lacuna import export
from lacuna.db import Database, LossSample, TputSample, parse_since


def mem(case):
    db = Database(":memory:")
    case.addCleanup(db.close)
    return db


class DbTests(unittest.TestCase):
    def test_schema_and_version(self):
        db = mem(self)
        tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"meta", "runs", "loss_samples", "tput_samples"} <= tables)
        self.assertEqual(db.schema_version(), 1)

    def test_migrate_is_idempotent_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "x.db"
            db = Database(path)
            rid = db.add_run("p", "udp", "h:1")
            db.close()
            db2 = Database(path)
            self.addCleanup(db2.close)
            self.assertEqual(db2.schema_version(), 1)
            self.assertEqual(db2.counts()["runs"], 1)
            self.assertEqual(rid, 1)

    def test_loss_roundtrip(self):
        db = mem(self)
        rid = db.add_run("wifi", "udp", "h:9400", {"count": 10}, ts=1000.0)
        db.add_loss(rid, LossSample(sent=10, recv=9, loss_pct=10.0, up_loss_pct=5.0, rtt_p50=12.5,
                                    loss_bits_hex="40", max_burst=1))
        (row,) = db.loss_rows()
        self.assertEqual((row["profile"], row["kind"], row["ts"]), ("wifi", "udp", 1000.0))
        self.assertEqual(row["sent"], 10)
        self.assertEqual(row["up_loss_pct"], 5.0)
        self.assertIsNone(row["down_loss_pct"])
        self.assertEqual(row["loss_bits_hex"], "40")
        self.assertIn('"count": 10', row["params_json"])

    def test_tput_roundtrip_with_nulls(self):
        db = mem(self)
        rid = db.add_run("p", "throughput", "h:1")
        db.add_tput(rid, TputSample("down", 2, 5.0, 1_000_000, 1.6))
        (row,) = db.tput_rows()
        self.assertEqual(row["mbps"], 1.6)
        self.assertIsNone(row["retrans"])

    def test_filters(self):
        db = mem(self)
        for prof, ts in (("a", 100.0), ("a", 200.0), ("b", 300.0)):
            db.add_loss(db.add_run(prof, "icmp", "x", ts=ts), LossSample(1, 1, 0.0))
        self.assertEqual(len(db.loss_rows()), 3)
        self.assertEqual(len(db.loss_rows(["a"])), 2)
        self.assertEqual(len(db.loss_rows(["a", "b"], since=150.0)), 2)
        self.assertEqual(len(db.loss_rows(since=1e9)), 0)
        self.assertEqual(db.profiles(), ["a", "b"])
        self.assertEqual([r["ts"] for r in db.loss_rows()], [100.0, 200.0, 300.0])


class SinceTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_since("7d", now=1_000_000), 1_000_000 - 7 * 86400)
        self.assertEqual(parse_since("90m", now=10_000), 10_000 - 5400)
        self.assertEqual(parse_since("2w", now=2_000_000), 2_000_000 - 1_209_600)
        self.assertIsNone(parse_since(None))

    def test_bad_value(self):
        for bad in ("7", "d", "7y", "abc"):
            with self.assertRaises(ValueError):
                parse_since(bad)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.db = mem(self)
        rid = self.db.add_run("p", "udp", "h:1", ts=50.0)
        self.db.add_loss(rid, LossSample(10, 9, 10.0, rtt_p50=5.0, loss_bits_hex="40"))
        rid = self.db.add_run("p", "throughput", "h:1", ts=60.0)
        self.db.add_tput(rid, TputSample("up", 1, 2.0, 500, 0.002))

    def test_json(self):
        import json

        doc = json.loads(export.to_json(self.db))
        self.assertEqual(len(doc["loss_samples"]), 1)
        self.assertEqual(len(doc["tput_samples"]), 1)
        self.assertEqual(doc["loss_samples"][0]["rtt_p50"], 5.0)
        self.assertNotIn("params_json", doc["loss_samples"][0])
        self.assertEqual(doc["version"], 1)

    def test_csv(self):
        import csv
        import io

        rows = list(csv.DictReader(io.StringIO(export.to_csv(self.db))))
        self.assertEqual([r["sample"] for r in rows], ["loss", "tput"])
        self.assertEqual(rows[0]["loss_pct"], "10.0")
        self.assertEqual(rows[0]["mbps"], "")
        self.assertEqual(rows[1]["mbps"], "0.002")


class XdgTests(unittest.TestCase):
    def test_data_dir_respects_xdg(self):
        from lacuna import config

        old = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = "/tmp/xdg-test"
        try:
            self.assertEqual(str(config.default_db_path()), "/tmp/xdg-test/lacuna/lacuna.db")
        finally:
            if old is None:
                del os.environ["XDG_DATA_HOME"]
            else:
                os.environ["XDG_DATA_HOME"] = old


if __name__ == "__main__":
    unittest.main()
