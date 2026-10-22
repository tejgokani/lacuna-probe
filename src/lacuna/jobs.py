"""Turn form-style values (all strings) into a validated Job."""

from __future__ import annotations

from lacuna.config import ConfigError, Job, validate_job
from lacuna.probes import split_host_port
from lacuna.protocol import MAX_UDP_COUNT

JOB_KIND = {"ping": "icmp", "udp": "udp", "tcp": "tcp", "throughput": "throughput", "http": "http"}


def _num(v: dict[str, str], key: str, cast: type, label: str, low: float = 0, high: float | None = None):
    try:
        val = cast(v[key].strip())
    except ValueError:
        raise ValueError(f"{label} must be a number") from None
    if val <= low:
        raise ValueError(f"{label} must be above {low:g}")
    if high is not None and val > high:
        raise ValueError(f"{label} must be at most {high:g}")
    return val


def build_job(kind: str, v: dict[str, str]) -> Job:
    """Validate form values the same way the CLI does, without --unsafe-rate."""
    target = v["target"].strip()
    if not target:
        raise ValueError("enter a target first")
    params: dict = {}
    if kind in ("ping", "tcp"):
        params = {"count": _num(v, "count", int, "packets", 0, 10000),
                  "interval": _num(v, "interval", float, "interval")}
    elif kind == "udp":
        params = {"count": _num(v, "count", int, "packets", 0, MAX_UDP_COUNT),
                  "pps": _num(v, "pps", float, "packets/s"),
                  "size": _num(v, "size", int, "packet bytes", 31, 1400)}
    else:
        params = {"secs": _num(v, "secs", float, "seconds", 0, 60),
                  "streams": _num(v, "streams", int, "streams", 0, 16 if kind == "http" else 8)}
        if kind == "throughput":
            params["direction"] = v["direction"]
    if kind in ("udp", "tcp", "throughput"):
        split_host_port(target)
    job = Job(JOB_KIND[kind], target, 0, params)
    try:
        validate_job(job)
    except ConfigError as exc:
        msg = str(exc).split(". Pass --unsafe-rate")[0]
        raise ValueError(msg + ". Use the CLI with --unsafe-rate to go higher.") from None
    return job


