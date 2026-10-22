"""Saved targets: a name, an address, notes, and custom operations to run against it.

Stored as JSON in the config directory. An operation is one Lacuna probe with its own
parameters. If it has an `every` schedule, the monitor runs it automatically.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from lacuna.config import Job, config_dir, parse_duration
from lacuna.jobs import build_job

OP_KINDS = ("ping", "tcp", "udp", "throughput", "http")
MIN_EVERY_S = 5.0

# kind -> [(key, label, default, hint)]
OP_FIELDS: dict[str, list[tuple[str, str, str, str]]] = {
    "ping": [("count", "Packets", "20", ""), ("interval", "Interval (s)", "0.2", "0.2 = 5 pps, cap 20 pps")],
    "tcp": [("port", "Port", "443", "any TCP port"), ("count", "Connects", "20", ""),
            ("interval", "Interval (s)", "0.5", "cap 10 per second")],
    "udp": [("port", "Server port", "9400", "a running lacuna server"), ("count", "Packets", "500", ""),
            ("pps", "Packets/s", "20", "cap 200"), ("size", "Packet bytes", "200", "32 to 1400")],
    "throughput": [("port", "Server port", "9400", "a running lacuna server"), ("secs", "Seconds", "10", "max 60"),
                   ("streams", "Streams", "1", "1 to 8"), ("direction", "Direction", "down", "up or down")],
    "http": [("url", "URL", "http://{host}/", "{host} is replaced by the address"),
             ("secs", "Seconds", "10", "max 60"), ("streams", "Streams", "4", "1 to 16")],
}
KIND_LABEL = {"ping": "Ping (ICMP)", "tcp": "TCP connect", "udp": "UDP probe",
              "throughput": "Throughput (Lacuna server)", "http": "HTTP download"}

_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")


@dataclass
class Op:
    kind: str = "ping"
    label: str = ""
    params: dict[str, str] = field(default_factory=dict)
    every: str = ""  # empty = run by hand only

    def value(self, key: str) -> str:
        for k, _, default, _ in OP_FIELDS[self.kind]:
            if k == key:
                return str(self.params.get(key, default))
        return ""

    def summary(self) -> str:
        bits = [KIND_LABEL[self.kind]]
        bits += [f"{k} {self.value(k)}" for k, *_ in OP_FIELDS[self.kind][:2] if k != "url"]
        text = ", ".join(bits)
        return f"{self.label}: {text}" if self.label else text


@dataclass
class Target:
    name: str
    host: str
    notes: str = ""
    ops: list[Op] = field(default_factory=list)

    def scheduled(self) -> int:
        return sum(1 for o in self.ops if o.every)


def targets_path() -> Path:
    return config_dir() / "targets.json"


def check_host(host: str) -> str:
    host = host.strip()
    if not _HOST.match(host) or len(host) > 253:
        raise ValueError("address must be a host name or IPv4 address such as 10.0.0.1 or example.org")
    return host


def check_every(text: str) -> str:
    text = text.strip()
    if text and parse_duration(text) < MIN_EVERY_S:
        raise ValueError(f"schedule must be at least {MIN_EVERY_S:g} seconds, for example 5m or 1h")
    return text


def op_job(target: Target, op: Op) -> Job:
    """Build and validate the Job for one operation. Raises ValueError."""
    v = {k: op.value(k) for k, *_ in OP_FIELDS[op.kind]}
    host = check_host(target.host)
    if op.kind == "ping":
        v["target"] = host
    elif op.kind == "http":
        v["target"] = v.pop("url").replace("{host}", host)
        if not v["target"].startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
    else:
        v["target"] = f"{host}:{v.pop('port').strip()}"
    job = build_job(op.kind, v)
    job.every_s = parse_duration(check_every(op.every)) if op.every.strip() else 0
    return job


def scheduled_jobs(targets: list[Target]) -> tuple[list[Job], list[str]]:
    """Jobs for every operation with a schedule, and a message for each one that is invalid."""
    jobs: list[Job] = []
    errors: list[str] = []
    for t in targets:
        for i, op in enumerate(t.ops, 1):
            if not op.every.strip():
                continue
            try:
                jobs.append(op_job(t, op))
            except ValueError as exc:
                errors.append(f"{t.name} operation {i}: {exc}")
    return jobs, errors


def find(targets: list[Target], name: str) -> Target | None:
    low = name.strip().lower()
    return next((t for t in targets if t.name.lower() == low), None)


# ---- storage ----

def _to_dict(t: Target) -> dict:
    return {"name": t.name, "host": t.host, "notes": t.notes,
            "ops": [{"kind": o.kind, "label": o.label, "params": o.params, "every": o.every} for o in t.ops]}


def _from_dict(raw: object) -> Target | None:
    if not isinstance(raw, dict) or not raw.get("name") or not raw.get("host"):
        return None
    ops = []
    for o in raw.get("ops", []) if isinstance(raw.get("ops"), list) else []:
        if isinstance(o, dict) and o.get("kind") in OP_KINDS:
            params = o.get("params") if isinstance(o.get("params"), dict) else {}
            ops.append(Op(o["kind"], str(o.get("label", "")), {str(k): str(v) for k, v in params.items()},
                          str(o.get("every", ""))))
    return Target(str(raw["name"]), str(raw["host"]), str(raw.get("notes", "")), ops)


def load(path: Path | None = None) -> list[Target]:
    """Read saved targets. A missing or damaged file gives an empty list."""
    try:
        raw = json.loads((path or targets_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = raw.get("targets", []) if isinstance(raw, dict) else []
    out = [t for t in (_from_dict(x) for x in items) if t is not None]
    return out


def save(targets: list[Target], path: Path | None = None) -> None:
    path = path or targets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "targets": [_to_dict(t) for t in targets]}, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
