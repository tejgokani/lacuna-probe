"""CSV and JSON export of stored samples."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from dataclasses import fields

from lacuna import __version__
from lacuna.db import Database, LossSample, TputSample

_BASE = ["ts", "profile", "kind", "target"]
_LOSS = [f.name for f in fields(LossSample)]
_TPUT = [f.name for f in fields(TputSample)]
# loss and tput share no column names, so one flat table holds both
CSV_COLUMNS = ["sample"] + _BASE + _LOSS + _TPUT


def _clean(row: sqlite3.Row, cols: list[str]) -> dict:
    return {c: row[c] for c in _BASE + cols}


def to_json(db: Database, profiles: list[str] | None = None, since: float | None = None) -> str:
    doc = {
        "version": 1,
        "lacuna": __version__,
        "loss_samples": [_clean(r, _LOSS) for r in db.loss_rows(profiles, since)],
        "tput_samples": [_clean(r, _TPUT) for r in db.tput_rows(profiles, since)],
    }
    return json.dumps(doc, indent=2) + "\n"


def to_csv(db: Database, profiles: list[str] | None = None, since: float | None = None) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    w.writeheader()
    rows = [("loss", _LOSS, r) for r in db.loss_rows(profiles, since)]
    rows += [("tput", _TPUT, r) for r in db.tput_rows(profiles, since)]
    rows.sort(key=lambda t: t[2]["ts"])
    for sample, cols, r in rows:
        w.writerow({"sample": sample, **_clean(r, cols)})
    return buf.getvalue()
