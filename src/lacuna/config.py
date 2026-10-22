"""Config file, defaults, rate caps and XDG paths."""

from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

DEFAULT_PORT = 9400
KINDS = ("icmp", "udp", "tcp", "throughput", "http")


class ConfigError(ValueError):
    pass


# ---- rate caps -------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Cap:
    default: float
    hard: float
    unit: str


RATE_CAPS = {
    "icmp": Cap(5, 20, "packets/s"),
    "udp": Cap(20, 200, "packets/s"),
    "tcp": Cap(2, 10, "connects/s"),
    "throughput": Cap(10, 60, "seconds per test"),
}


def check_rate(kind: str, value: float, unsafe: bool = False) -> str | None:
    """Raise ConfigError if value exceeds the hard cap. Return a warning when
    the cap was exceeded with `unsafe`, else None."""
    cap = RATE_CAPS[kind]
    if value <= cap.hard:
        return None
    msg = f"{kind}: {value:g} {cap.unit} exceeds the hard cap of {cap.hard:g}"
    if not unsafe:
        raise ConfigError(msg + ". Pass --unsafe-rate if you own both ends and the path.")
    return "warning: " + msg


# ---- paths -----------------------------------------------------------------

def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "lacuna"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "lacuna"


def default_db_path() -> Path:
    return data_dir() / "lacuna.db"


def default_config_path() -> Path:
    return config_dir() / "lacuna.toml"


def default_profile() -> str:
    return socket.gethostname() or "default"


# ---- jobs and config -------------------------------------------------------

_DUR = re.compile(r"^(\d+(?:\.\d+)?)\s*([smhd]?)$")
_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str | int | float) -> float:
    """'90', '30s', '15m', '2h', '7d' to seconds."""
    if isinstance(text, (int, float)):
        return float(text)
    m = _DUR.match(text.strip())
    if not m:
        raise ConfigError(f"bad duration {text!r}, use forms like 30s, 15m, 2h, 7d")
    return float(m.group(1)) * _UNITS[m.group(2)]


@dataclass(slots=True)
class Job:
    kind: str
    target: str
    every_s: float
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.kind} {self.target}"


@dataclass(slots=True)
class Config:
    profile: str
    db_path: Path
    jobs: list[Job]


def default_jobs() -> list[Job]:
    jobs = [Job("icmp", t, 300, {"count": 20, "interval": 0.2}) for t in ("1.1.1.1", "8.8.8.8", "9.9.9.9")]
    jobs += [Job("tcp", t, 300, {"count": 10, "interval": 0.5}) for t in ("1.1.1.1:443", "8.8.8.8:443")]
    url = "https://speed.cloudflare.com/__down?bytes=10000000"
    jobs.append(Job("http", url, 900, {"secs": 10, "streams": 4}))
    return jobs


def _parse_job(raw: dict[str, Any], idx: int) -> Job:
    kind = raw.get("kind")
    if kind not in KINDS:
        raise ConfigError(f"job {idx}: kind must be one of {', '.join(KINDS)}")
    target = raw.get("target")
    if not isinstance(target, str) or not target:
        raise ConfigError(f"job {idx}: target is required")
    every = parse_duration(raw.get("every", "5m"))
    if every < 1:
        raise ConfigError(f"job {idx}: every must be at least 1s")
    params = {k: v for k, v in raw.items() if k not in ("kind", "target", "every")}
    job = Job(kind, target, every, params)
    validate_job(job, idx)
    return job


def validate_job(job: Job, idx: int = 0) -> None:
    p = job.params
    try:
        if job.kind == "icmp":
            check_rate("icmp", 1.0 / float(p.get("interval", 0.2)))
        elif job.kind == "udp":
            check_rate("udp", float(p.get("pps", 20)))
        elif job.kind == "tcp":
            check_rate("tcp", 1.0 / float(p.get("interval", 0.5)))
        else:
            check_rate("throughput", float(p.get("secs", 10)))
    except ConfigError as exc:
        raise ConfigError(f"job {idx}: {exc}") from None
    if job.kind in ("udp", "tcp", "throughput") and ":" not in job.target:
        raise ConfigError(f"job {idx}: target must be HOST:PORT")


def load_config(path: str | os.PathLike | None, profile: str | None = None) -> Config:
    """Load a TOML file. With path None, use the built-in default targets."""
    raw: dict[str, Any] = {}
    if path is not None:
        if tomllib is None:
            raise ConfigError("reading TOML needs the tomli package on Python 3.10")
        try:
            with open(path, "rb") as fh:
                raw = tomllib.load(fh)
        except FileNotFoundError:
            raise ConfigError(f"config file not found: {path}") from None
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from None
    prof = profile or raw.get("profile") or default_profile()
    db = Path(raw["db"]).expanduser() if raw.get("db") else default_db_path()
    raw_jobs = raw.get("job")
    if raw_jobs is None:
        jobs = default_jobs()
    elif isinstance(raw_jobs, list) and raw_jobs:
        jobs = [_parse_job(j, i + 1) for i, j in enumerate(raw_jobs)]
    else:
        raise ConfigError("[[job]] must be a non-empty list of tables")
    return Config(profile=str(prof), db_path=db, jobs=jobs)
