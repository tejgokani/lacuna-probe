"""State shared by all screens, plus the background monitor and server."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from lacuna.config import Config, default_config_path, load_config
from lacuna.db import Database
from lacuna.demo import demo_db_path
from lacuna.monitor import Monitor
from lacuna.server import LacunaServer
from lacuna.tui.settings import Settings


class MonitorController:
    """Runs `lacuna monitor` inside the app, on its own thread and connection."""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.log: deque[str] = deque(maxlen=300)
        self.cfg: Config | None = None
        self.monitor: Monitor | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def config_path(self) -> str | None:
        if self.ctx.settings.config_path:
            return str(Path(self.ctx.settings.config_path).expanduser())
        default = default_config_path()
        return str(default) if default.exists() else None

    def load_config(self) -> Config:
        cfg = load_config(self.config_path(), self.ctx.settings.effective_profile())
        cfg.db_path = self.ctx.db_path_real()
        return cfg

    def start(self) -> None:
        if self.running:
            return
        self.cfg = self.load_config()  # ConfigError goes to the caller
        self._stop = threading.Event()
        self.log.append(f"{time.strftime('%H:%M:%S')} started, {len(self.cfg.jobs)} jobs, profile {self.cfg.profile}")
        self._thread = threading.Thread(target=self._run, args=(self.cfg, self._stop), daemon=True, name="monitor")
        self._thread.start()

    def _run(self, cfg: Config, stop: threading.Event) -> None:
        try:
            with Database(cfg.db_path) as db:
                self.monitor = Monitor(cfg, db, stop, self.log.append)
                self.monitor.run()
        except Exception as exc:
            self.log.append(f"{time.strftime('%H:%M:%S')} monitor stopped by error: {exc}")
        else:
            self.log.append(f"{time.strftime('%H:%M:%S')} stopped")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    @property
    def runs(self) -> int:
        return self.monitor.runs if self.monitor else 0

    @property
    def failures(self) -> int:
        return self.monitor.failures if self.monitor else 0


class ServerController:
    """Runs `lacuna server` inside the app."""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.server: LacunaServer | None = None

    @property
    def running(self) -> bool:
        return self.server is not None

    def start(self) -> None:
        if self.server is not None:
            return
        s = self.ctx.settings
        self.server = LacunaServer(s.server_bind, s.server_port)
        try:
            self.server.start()
        except OSError:
            self.server = None
            raise

    def stop(self) -> None:
        if self.server is not None:
            self.server.stop()
            self.server = None


class Context:
    """Everything screens share. `switch` is set by the app."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.use_demo = False
        self.db_override: Path | None = None  # from --db, this session only
        self.status: tuple[str, str, float] | None = None
        self.switch: Callable[[str], None] = lambda name: None
        self.monitor = MonitorController(self)
        self.server = ServerController(self)

    def set_status(self, msg: str, level: str = "ok") -> None:
        self.status = (msg, level, time.monotonic())

    def db_path(self) -> Path:
        """Database the dashboard reads: demo data or the real one."""
        return demo_db_path() if self.use_demo else self.db_path_real()

    def db_path_real(self) -> Path:
        """Database that probes and the monitor write to. Never the demo one."""
        return self.db_override or self.settings.effective_db()

    def open_db(self) -> Database:
        return Database(self.db_path())

    def shutdown(self) -> None:
        self.monitor.stop()
        self.server.stop()


__all__ = ["Context", "MonitorController", "ServerController"]
