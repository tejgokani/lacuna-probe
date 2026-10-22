"""Full-screen terminal app. Run it with a bare `lacuna`."""

from __future__ import annotations

import sys

from lacuna.config import default_db_path
from lacuna.db import Database
from lacuna.tui.dashboard import build_lines, sparkline

__all__ = ["build_lines", "run_tui", "sparkline"]


def run_tui(db_path: str | None = None, profile: str | None = None) -> int:
    """Open the app on a terminal, or print one dashboard snapshot when piped."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        with Database(db_path or default_db_path()) as db:
            print("\n".join(build_lines(db, profile)))
        return 0
    from lacuna.tui.app import run_app

    return run_app(db_path, profile)
