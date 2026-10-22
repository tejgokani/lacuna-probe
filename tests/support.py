"""Shared test helpers. unittest discovery puts this folder on sys.path."""

import os
import tempfile
import time
import unittest
from pathlib import Path

from lacuna.db import Database, LossSample, TputSample
from lacuna.tui import settings as S
from lacuna.tui.services import Context

PORT = 9470


class Env(unittest.TestCase):
    """Isolated XDG dirs and a fresh Context per test."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        saved = {k: os.environ.get(k) for k in ("XDG_DATA_HOME", "XDG_CONFIG_HOME")}
        os.environ["XDG_DATA_HOME"] = str(self.tmp / "data")
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "conf")
        self.addCleanup(self._restore, saved)
        self.settings = S.Settings(db_path=str(self.tmp / "t.db"), output_dir=str(self.tmp / "out"),
                                   server_bind="127.0.0.1", server_port=PORT)
        self.ctx = Context(self.settings)
        self.addCleanup(self.ctx.shutdown)

    @staticmethod
    def _restore(saved):
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def seed(self, profile="wifi", n=3):
        with Database(self.settings.db_path) as db:
            for i in range(n):
                rid = db.add_run(profile, "icmp", "1.1.1.1", ts=time.time() - 60 * (n - i))
                db.add_loss(rid, LossSample(100, 98, 2.0, rtt_p50=10.0, rtt_p95=14.0, jitter_ms=0.7,
                                            max_burst=2, loss_bits_hex="00" * 13))
            rid = db.add_run(profile, "throughput", "h:1", ts=time.time() - 30)
            db.add_tput(rid, TputSample("down", 1, 5.0, 1_000_000, 42.0))

    def wait(self, screen, timeout=15.0):
        end = time.time() + timeout
        while time.time() < end:
            screen.tick(time.time())
            if getattr(screen, "task", None) is None:
                return
            time.sleep(0.05)
        self.fail("task did not finish")
