"""UDP wire format shared by the probe client and the Lacuna server.

Header, little endian, 24 bytes:

    magic[4]  version[1]  type[1]  flags[1]  reserved[1]
    session u32  seq u32  t_send_ns u64

Every packet after HELLO carries an 8 byte cookie right after the header.
The server never sends a packet larger than the packet it answers.
"""

from __future__ import annotations

import hashlib
import hmac
import struct
import time
from dataclasses import dataclass

MAGIC = b"LCNA"
VERSION = 1

HELLO, HELLO_ACK, PROBE, ECHO, STATS_REQ, STATS = range(6)
TYPE_NAMES = {
    HELLO: "HELLO",
    HELLO_ACK: "HELLO_ACK",
    PROBE: "PROBE",
    ECHO: "ECHO",
    STATS_REQ: "STATS_REQ",
    STATS: "STATS",
}

_HEADER = struct.Struct("<4sBBBBIIQ")
HEADER_LEN = _HEADER.size  # 24
COOKIE_LEN = 8
MIN_PROBE = HEADER_LEN + COOKIE_LEN  # 32
MIN_HELLO = 48  # larger than HELLO_ACK (32) so the handshake never amplifies
MAX_PACKET = 1400
STATS_FIXED = HEADER_LEN + 8  # header + rx_count u32 + max_seq u32
MAX_BITMAP_BITS = (MAX_PACKET - STATS_FIXED) * 8
MAX_UDP_COUNT = MAX_BITMAP_BITS


class ProtocolError(ValueError):
    """Raised when a datagram is not a valid Lacuna packet."""


@dataclass(frozen=True, slots=True)
class Header:
    type: int
    session: int
    seq: int
    t_send_ns: int
    flags: int = 0


def pack_header(h: Header) -> bytes:
    return _HEADER.pack(MAGIC, VERSION, h.type, h.flags, 0, h.session, h.seq, h.t_send_ns)


def unpack_header(data: bytes) -> Header:
    if len(data) < HEADER_LEN:
        raise ProtocolError("short packet")
    magic, ver, ty, flags, _rsv, session, seq, t_ns = _HEADER.unpack_from(data)
    if magic != MAGIC:
        raise ProtocolError("bad magic")
    if ver != VERSION:
        raise ProtocolError("bad version")
    if ty not in TYPE_NAMES:
        raise ProtocolError("bad type")
    return Header(ty, session, seq, t_ns, flags)


def make_cookie(secret: bytes, ip: str, port: int, session: int) -> bytes:
    msg = f"{ip}|{port}|{session}".encode()
    return hmac.new(secret, msg, hashlib.sha256).digest()[:COOKIE_LEN]


def cookie_ok(expected: bytes, got: bytes) -> bool:
    return len(got) == COOKIE_LEN and hmac.compare_digest(expected, got)


def build_hello(session: int, size: int = MIN_HELLO) -> bytes:
    size = max(MIN_HELLO, min(size, MAX_PACKET))
    return pack_header(Header(HELLO, session, 0, 0)).ljust(size, b"\0")


def build_hello_ack(session: int, cookie: bytes) -> bytes:
    return pack_header(Header(HELLO_ACK, session, 0, 0)) + cookie


def build_probe(session: int, seq: int, t_ns: int, cookie: bytes, size: int) -> bytes:
    size = max(MIN_PROBE, min(size, MAX_PACKET))
    return (pack_header(Header(PROBE, session, seq, t_ns)) + cookie).ljust(size, b"\0")


def to_echo(probe: bytes) -> bytes:
    """Turn a PROBE datagram into its ECHO. Same length, same payload."""
    return probe[:5] + bytes([ECHO]) + probe[6:]


def stats_req_size(count: int) -> int:
    """Request size that leaves room for a full bitmap of `count` probes."""
    need = STATS_FIXED + (count + 7) // 8
    return max(MIN_PROBE, min(need, MAX_PACKET))


def build_stats_req(session: int, cookie: bytes, size: int) -> bytes:
    size = max(MIN_PROBE, min(size, MAX_PACKET))
    return (pack_header(Header(STATS_REQ, session, 0, 0)) + cookie).ljust(size, b"\0")


def build_stats(session: int, rx_count: int, max_seq: int, bitmap: bytes, limit: int) -> bytes:
    """Build a STATS reply no longer than `limit` bytes (the request size)."""
    room = max(0, limit - STATS_FIXED)
    body = struct.pack("<II", rx_count, max_seq) + bitmap[:room]
    return pack_header(Header(STATS, session, 0, 0)) + body


@dataclass(frozen=True, slots=True)
class Stats:
    rx_count: int
    max_seq: int
    bitmap: bytes

    def received(self, seq: int) -> bool:
        byte, bit = divmod(seq, 8)
        return byte < len(self.bitmap) and bool(self.bitmap[byte] >> bit & 1)


def parse_stats(data: bytes) -> Stats:
    if len(data) < STATS_FIXED:
        raise ProtocolError("short stats")
    rx, max_seq = struct.unpack_from("<II", data, HEADER_LEN)
    return Stats(rx, max_seq, bytes(data[STATS_FIXED:]))


def probe_cookie(data: bytes) -> bytes:
    return bytes(data[HEADER_LEN : HEADER_LEN + COOKIE_LEN])


def now_ns() -> int:
    return time.monotonic_ns()
