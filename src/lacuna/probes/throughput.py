"""TCP sink/source test against a Lacuna server, and an HTTP download test."""

from __future__ import annotations

import contextlib
import http.client
import socket
import struct
import threading
import time
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass

from lacuna import server as S
from lacuna.config import data_dir
from lacuna.db import TputSample
from lacuna.probes import resolve_ipv4

try:
    import fcntl
except ImportError:  # not POSIX
    fcntl = None  # type: ignore[assignment]

MAX_SECS = 60
_CHUNK = b"\0" * S.TCP_CHUNK

# struct tcp_info up to total_retrans: 8 single bytes, then 24 u32 (linux/tcp.h)
_TCP_INFO = struct.Struct("=8B24I")
_TCP_INFO_FIELDS = (
    "rto ato snd_mss rcv_mss unacked sacked lost retrans fackets "
    "last_data_sent last_ack_sent last_data_recv last_ack_recv "
    "pmtu rcv_ssthresh rtt rttvar snd_ssthresh snd_cwnd advmss reordering "
    "rcv_rtt rcv_space total_retrans"
).split()


class ThroughputError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TcpInfo:
    total_retrans: int
    snd_cwnd: int
    rtt_us: int
    rttvar_us: int
    snd_ssthresh: int


def parse_tcp_info(raw: bytes) -> TcpInfo | None:
    if len(raw) < _TCP_INFO.size:
        return None
    vals = dict(zip(_TCP_INFO_FIELDS, _TCP_INFO.unpack_from(raw)[8:], strict=False))
    return TcpInfo(vals["total_retrans"], vals["snd_cwnd"], vals["rtt"], vals["rttvar"],
                   vals["snd_ssthresh"])


def read_tcp_info(sock: socket.socket) -> TcpInfo | None:
    """TCP_INFO on Linux. None elsewhere or on any error."""
    opt = getattr(socket, "TCP_INFO", None)
    if opt is None:
        return None
    try:
        return parse_tcp_info(sock.getsockopt(socket.IPPROTO_TCP, opt, 192))
    except OSError:
        return None


def summarize_info(
    before: list[TcpInfo | None], after: list[TcpInfo | None], with_retrans: bool
) -> dict[str, int | float | None]:
    """Fold per-stream TCP_INFO into one row. Retransmits are deltas."""
    pairs = [(b, a) for b, a in zip(before, after, strict=False) if a is not None]
    if not pairs:
        return {"retrans": None, "cwnd": None, "rtt_ms": None, "ssthresh": None}
    retrans = sum(a.total_retrans - (b.total_retrans if b else 0) for b, a in pairs)
    n = len(pairs)
    return {
        "retrans": retrans if with_retrans else None,
        "cwnd": round(sum(a.snd_cwnd for _, a in pairs) / n),
        "rtt_ms": sum(a.rtt_us for _, a in pairs) / n / 1000.0,
        "ssthresh": round(sum(a.snd_ssthresh for _, a in pairs) / n),
    }


@contextlib.contextmanager
def one_at_a_time() -> Iterator[None]:
    """Refuse to run two throughput tests at once on this machine."""
    if fcntl is None:
        yield
        return
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "throughput.lock", "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ThroughputError("another throughput test is already running") from None
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _check_secs(secs: float) -> None:
    if not 0 < secs <= MAX_SECS:
        raise ThroughputError(f"secs must be in (0, {MAX_SECS}]")


@dataclass(slots=True)
class _Stream:
    bytes: int = 0
    before: TcpInfo | None = None
    after: TcpInfo | None = None
    error: str | None = None


def _tcp_stream(addr, port, direction, secs, out: _Stream) -> None:
    mode = S.TCP_UP if direction == "up" else S.TCP_DOWN
    try:
        with socket.create_connection((addr, port), timeout=5) as sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.sendall(S.TCP_HEADER.pack(S.TCP_MAGIC, mode, int(secs * 1000)))
            out.before = read_tcp_info(sock)
            if direction == "up":
                _push(sock, secs, out)
            else:
                _pull(sock, secs, out)
    except OSError as exc:
        out.error = str(exc)


def _push(sock: socket.socket, secs: float, out: _Stream) -> None:
    end = time.monotonic() + secs
    while time.monotonic() < end:
        sock.sendall(_CHUNK)
    out.after = read_tcp_info(sock)
    sock.shutdown(socket.SHUT_WR)
    sock.settimeout(10)
    raw = b""
    while len(raw) < 8:
        chunk = sock.recv(8 - len(raw))
        if not chunk:
            raise OSError("server closed before reporting bytes")
        raw += chunk
    out.bytes = struct.unpack("<Q", raw)[0]


def _pull(sock: socket.socket, secs: float, out: _Stream) -> None:
    sock.settimeout(secs + 10)
    while True:
        chunk = sock.recv(S.TCP_CHUNK)
        if not chunk:
            break
        out.bytes += len(chunk)
    out.after = read_tcp_info(sock)


def run_tcp_throughput(
    host: str, port: int, secs: float = 10, streams: int = 1, direction: str = "down",
) -> tuple[TputSample, list[float]]:
    """`port` is the server's base (UDP) port. The TCP sink listens on port + 1.
    Returns the sample and Mbit/s per stream."""
    _check_secs(secs)
    if direction not in ("up", "down"):
        raise ThroughputError("direction must be up or down")
    if not 1 <= streams <= S.TCP_MAX_CONN_PER_IP:
        raise ThroughputError(f"streams must be 1..{S.TCP_MAX_CONN_PER_IP}")
    addr = resolve_ipv4(host)
    outs = [_Stream() for _ in range(streams)]
    with one_at_a_time():
        t0 = time.monotonic()
        threads = [
            threading.Thread(target=_tcp_stream, args=(addr, port + 1, direction, secs, o))
            for o in outs
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.monotonic() - t0
    failed = [o.error for o in outs if o.error]
    if failed and len(failed) == streams:
        raise ThroughputError(f"all streams failed: {failed[0]}")
    return _finish(outs, elapsed, streams, direction)


def _finish(outs: list[_Stream], elapsed: float, streams: int, direction: str):
    total = sum(o.bytes for o in outs)
    info = summarize_info([o.before for o in outs], [o.after for o in outs], direction == "up")
    per = [o.bytes * 8 / elapsed / 1e6 for o in outs]
    sample = TputSample(direction, streams, elapsed, total, total * 8 / elapsed / 1e6, **info)
    return sample, per


# ---- HTTP ----

def _http_stream(url: urllib.parse.SplitResult, end: float, out: _Stream) -> None:
    cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    path = (url.path or "/") + (f"?{url.query}" if url.query else "")
    conn = cls(url.hostname, url.port, timeout=10)
    try:
        conn.connect()
        out.before = read_tcp_info(conn.sock)
        while time.monotonic() < end:
            conn.request("GET", path, headers={"User-Agent": "lacuna-probe", "Connection": "keep-alive"})
            resp = conn.getresponse()
            if resp.status != 200:
                raise OSError(f"HTTP {resp.status}")
            while time.monotonic() < end:
                chunk = resp.read(S.TCP_CHUNK)
                if not chunk:
                    break
                out.bytes += len(chunk)
            if time.monotonic() >= end:
                break
        out.after = read_tcp_info(conn.sock)
    except (OSError, http.client.HTTPException) as exc:
        out.error = str(exc)
    finally:
        conn.close()


def run_http_throughput(url: str, secs: float = 10, streams: int = 4):
    _check_secs(secs)
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ThroughputError("url must be http:// or https://")
    if not 1 <= streams <= 16:
        raise ThroughputError("streams must be 1..16")
    outs = [_Stream() for _ in range(streams)]
    with one_at_a_time():
        t0 = time.monotonic()
        end = t0 + secs
        threads = [threading.Thread(target=_http_stream, args=(parts, end, o)) for o in outs]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.monotonic() - t0
    if all(o.error for o in outs):
        raise ThroughputError(f"all streams failed: {outs[0].error}")
    return _finish(outs, elapsed, streams, "down")
