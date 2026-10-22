"""SQLite storage. One file, stdlib sqlite3, forward-only migrations."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_MIGRATIONS = {
    1: """
    CREATE TABLE runs(
        id INTEGER PRIMARY KEY, ts REAL NOT NULL, profile TEXT NOT NULL,
        kind TEXT NOT NULL, target TEXT NOT NULL, params_json TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE loss_samples(
        id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
        sent INTEGER NOT NULL, recv INTEGER NOT NULL, loss_pct REAL NOT NULL,
        up_loss_pct REAL, down_loss_pct REAL,
        rtt_min REAL, rtt_mean REAL, rtt_p50 REAL, rtt_p95 REAL, rtt_p99 REAL, rtt_max REAL,
        jitter_ms REAL, dup INTEGER, reordered INTEGER, max_burst INTEGER,
        ge_p REAL, ge_r REAL, loss_bits_hex TEXT);
    CREATE TABLE tput_samples(
        id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
        direction TEXT NOT NULL, streams INTEGER NOT NULL, secs REAL NOT NULL,
        bytes INTEGER NOT NULL, mbps REAL NOT NULL,
        retrans INTEGER, cwnd INTEGER, rtt_ms REAL, ssthresh INTEGER);
    CREATE INDEX idx_runs_ts ON runs(ts);
    CREATE INDEX idx_loss_run ON loss_samples(run_id);
    CREATE INDEX idx_tput_run ON tput_samples(run_id);
    """,
}


@dataclass(slots=True)
class LossSample:
    sent: int
    recv: int
    loss_pct: float
    up_loss_pct: float | None = None
    down_loss_pct: float | None = None
    rtt_min: float | None = None
    rtt_mean: float | None = None
    rtt_p50: float | None = None
    rtt_p95: float | None = None
    rtt_p99: float | None = None
    rtt_max: float | None = None
    jitter_ms: float | None = None
    dup: int | None = None
    reordered: int | None = None
    max_burst: int | None = None
    ge_p: float | None = None
    ge_r: float | None = None
    loss_bits_hex: str | None = None


@dataclass(slots=True)
class TputSample:
    direction: str
    streams: int
    secs: float
    bytes: int
    mbps: float
    retrans: int | None = None
    cwnd: int | None = None
    rtt_ms: float | None = None
    ssthresh: int | None = None


_LOSS_COLS = [f.name for f in fields(LossSample)]
_TPUT_COLS = [f.name for f in fields(TputSample)]

_SINCE = re.compile(r"^(\d+(?:\.\d+)?)([smhdw])$")
_SINCE_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_since(text: str | None, now: float | None = None) -> float | None:
    """'7d', '12h', '30m' to an absolute unix time. None stays None."""
    if not text:
        return None
    m = _SINCE.match(text.strip())
    if not m:
        raise ValueError(f"bad --since value {text!r}, use forms like 90m, 12h, 7d, 2w")
    return (time.time() if now is None else now) - float(m.group(1)) * _SINCE_UNITS[m.group(2)]


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.migrate()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- schema ----

    def schema_version(self) -> int:
        has_meta = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone()
        if not has_meta:
            return 0
        row = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(row["value"]) if row else 0

    def migrate(self) -> None:
        self.conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        current = self.schema_version()
        for version in sorted(v for v in _MIGRATIONS if v > current):
            with self.conn:
                self.conn.executescript(_MIGRATIONS[version])
                self.conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                    (str(version),),
                )

    # ---- writes ----

    def add_run(
        self, profile: str, kind: str, target: str, params: dict[str, Any] | None = None,
        ts: float | None = None,
    ) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO runs(ts, profile, kind, target, params_json) VALUES(?,?,?,?,?)",
                (time.time() if ts is None else ts, profile, kind, target,
                 json.dumps(params or {}, sort_keys=True)),
            )
        return int(cur.lastrowid or 0)

    def add_loss(self, run_id: int, s: LossSample) -> int:
        vals = asdict(s)
        cols = ", ".join(_LOSS_COLS)
        marks = ", ".join("?" for _ in _LOSS_COLS)
        with self.conn:
            cur = self.conn.execute(
                f"INSERT INTO loss_samples(run_id, {cols}) VALUES(?, {marks})",
                [run_id] + [vals[c] for c in _LOSS_COLS],
            )
        return int(cur.lastrowid or 0)

    def add_tput(self, run_id: int, s: TputSample) -> int:
        vals = asdict(s)
        cols = ", ".join(_TPUT_COLS)
        marks = ", ".join("?" for _ in _TPUT_COLS)
        with self.conn:
            cur = self.conn.execute(
                f"INSERT INTO tput_samples(run_id, {cols}) VALUES(?, {marks})",
                [run_id] + [vals[c] for c in _TPUT_COLS],
            )
        return int(cur.lastrowid or 0)

    # ---- reads ----

    def _where(self, profiles: list[str] | None, since: float | None) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if profiles:
            clauses.append(f"r.profile IN ({','.join('?' for _ in profiles)})")
            args += profiles
        if since is not None:
            clauses.append("r.ts >= ?")
            args.append(since)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", args

    def loss_rows(self, profiles: list[str] | None = None, since: float | None = None) -> list[sqlite3.Row]:
        where, args = self._where(profiles, since)
        sql = (
            "SELECT r.ts, r.profile, r.kind, r.target, r.params_json, l.* "
            "FROM loss_samples l JOIN runs r ON r.id = l.run_id" + where + " ORDER BY r.ts, l.id"
        )
        return self.conn.execute(sql, args).fetchall()

    def tput_rows(self, profiles: list[str] | None = None, since: float | None = None) -> list[sqlite3.Row]:
        where, args = self._where(profiles, since)
        sql = (
            "SELECT r.ts, r.profile, r.kind, r.target, r.params_json, t.* "
            "FROM tput_samples t JOIN runs r ON r.id = t.run_id" + where + " ORDER BY r.ts, t.id"
        )
        return self.conn.execute(sql, args).fetchall()

    def profiles(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT profile FROM runs ORDER BY profile").fetchall()
        return [r["profile"] for r in rows]

    def counts(self) -> dict[str, int]:
        q = self.conn.execute
        return {
            "runs": q("SELECT COUNT(*) FROM runs").fetchone()[0],
            "loss_samples": q("SELECT COUNT(*) FROM loss_samples").fetchone()[0],
            "tput_samples": q("SELECT COUNT(*) FROM tput_samples").fetchone()[0],
        }
