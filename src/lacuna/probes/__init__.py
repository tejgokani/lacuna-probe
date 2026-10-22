"""Probes. Each returns a LossSample or TputSample ready for the database."""

from __future__ import annotations

import socket


def split_host_port(target: str, default_port: int | None = None) -> tuple[str, int]:
    """'host:port' to (host, port). Plain IPv4 and hostnames only."""
    host, sep, port = target.rpartition(":")
    if not sep:
        if default_port is None:
            raise ValueError(f"expected HOST:PORT, got {target!r}")
        return target, default_port
    if not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError(f"expected HOST:PORT, got {target!r}")
    return host, int(port)


def resolve_ipv4(host: str) -> str:
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except socket.gaierror as exc:
        raise OSError(f"cannot resolve {host}: {exc}") from None
