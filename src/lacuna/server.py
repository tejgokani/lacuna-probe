"""Lacuna server: UDP echo with anti-abuse rules, plus a TCP sink/source.

UDP rules, all enforced in ServerState.handle():
  * a reply is never larger than the request that caused it
  * probes without the right cookie are dropped
  * 200 packets/s per source IP, 8 live sessions per IP, 60 s idle expiry
  * packets over 1400 bytes are dropped
"""

from __future__ import annotations

import os
import socket
import struct
import threading
import time
from dataclasses import dataclass, field

from lacuna import protocol as P
from lacuna.config import DEFAULT_PORT

RATE_PER_IP = 200  # packets per second
MAX_SESSIONS_PER_IP = 8
SESSION_IDLE_S = 60.0
FINISHED_LINGER_S = 5.0  # a session that fetched its stats is finished; free the slot soon
MAX_SESSIONS_TOTAL = 4096

TCP_MAGIC = b"LCTP"
TCP_UP, TCP_DOWN = b"U", b"D"  # direction from the client's point of view
TCP_HEADER = struct.Struct("<4scI")  # magic, mode, duration in ms
TCP_MAX_MS = 60_000
TCP_MAX_CONN = 16
TCP_MAX_CONN_PER_IP = 8
TCP_CHUNK = 64 * 1024


@dataclass(slots=True)
class Session:
    cookie: bytes
    last_seen: float
    idle_limit: float = SESSION_IDLE_S
    rx_count: int = 0
    max_seq: int = 0
    bitmap: bytearray = field(default_factory=bytearray)

    def mark(self, seq: int) -> None:
        if seq >= P.MAX_BITMAP_BITS:
            self.rx_count += 1
            self.max_seq = max(self.max_seq, seq)
            return
        byte, bit = divmod(seq, 8)
        if byte >= len(self.bitmap):
            self.bitmap.extend(bytes(byte + 1 - len(self.bitmap)))
        if not self.bitmap[byte] >> bit & 1:
            self.bitmap[byte] |= 1 << bit
            self.rx_count += 1
        self.max_seq = max(self.max_seq, seq)


class ServerState:
    """Pure packet logic. Give it a datagram, get back the reply or None."""

    def __init__(self, secret: bytes | None = None):
        self.secret = secret or os.urandom(32)
        self.sessions: dict[tuple[str, int, int], Session] = {}
        self._rate: dict[str, list[float]] = {}  # ip -> [window_start, count]
        self.dropped = 0

    def handle(self, data: bytes, addr: tuple[str, int], now: float | None = None) -> bytes | None:
        now = time.monotonic() if now is None else now
        reply = self._handle(data, addr, now)
        if reply is None:
            self.dropped += 1
        return reply

    def _handle(self, data: bytes, addr: tuple[str, int], now: float) -> bytes | None:
        if len(data) > P.MAX_PACKET:
            return None
        try:
            hdr = P.unpack_header(data)
        except P.ProtocolError:
            return None
        ip, port = addr[0], addr[1]
        if not self._allow(ip, now):
            return None
        if hdr.type == P.HELLO:
            return self._hello(data, hdr, ip, port, now)
        key = (ip, port, hdr.session)
        sess = self.sessions.get(key)
        if sess is None or now - sess.last_seen > sess.idle_limit:
            self.sessions.pop(key, None)
            return None
        if len(data) < P.MIN_PROBE or not P.cookie_ok(sess.cookie, P.probe_cookie(data)):
            return None
        sess.last_seen = now
        if hdr.type == P.PROBE:
            sess.mark(hdr.seq)
            return P.to_echo(data)
        if hdr.type == P.STATS_REQ:
            sess.idle_limit = FINISHED_LINGER_S
            return P.build_stats(hdr.session, sess.rx_count, sess.max_seq, bytes(sess.bitmap), len(data))
        return None

    def _hello(self, data: bytes, hdr: P.Header, ip: str, port: int, now: float) -> bytes | None:
        if len(data) < P.MIN_HELLO:
            return None
        key = (ip, port, hdr.session)
        sess = self.sessions.get(key)
        if sess is None:
            self._expire(now)
            if sum(1 for k in self.sessions if k[0] == ip) >= MAX_SESSIONS_PER_IP:
                return None
            if len(self.sessions) >= MAX_SESSIONS_TOTAL:
                return None
            cookie = P.make_cookie(self.secret, ip, port, hdr.session)
            sess = self.sessions[key] = Session(cookie, now)
        sess.last_seen = now
        return P.build_hello_ack(hdr.session, sess.cookie)

    def _allow(self, ip: str, now: float) -> bool:
        win = self._rate.get(ip)
        if win is None or now - win[0] >= 1.0:
            if len(self._rate) > 10_000:
                self._rate.clear()
            self._rate[ip] = [now, 1]
            return True
        win[1] += 1
        return win[1] <= RATE_PER_IP

    def _expire(self, now: float) -> None:
        dead = [k for k, s in self.sessions.items() if now - s.last_seen > s.idle_limit]
        for k in dead:
            del self.sessions[k]


class LacunaServer:
    """UDP echo on `port`, TCP throughput on `port + 1`."""

    def __init__(self, bind: str = "0.0.0.0", port: int = DEFAULT_PORT):
        self.bind = bind
        self.port = port
        self.state = ServerState()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._udp: socket.socket | None = None
        self._tcp: socket.socket | None = None
        self._conns: dict[str, int] = {}
        self._lock = threading.Lock()

    @property
    def tcp_port(self) -> int:
        return self.port + 1

    def start(self) -> LacunaServer:
        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp.bind((self.bind, self.port))
        self._udp.settimeout(0.25)
        self._tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._tcp.bind((self.bind, self.tcp_port))
        self._tcp.listen(32)
        self._tcp.settimeout(0.25)
        for fn in (self._udp_loop, self._tcp_loop):
            t = threading.Thread(target=fn, daemon=True, name=fn.__name__)
            t.start()
            self._threads.append(t)
        return self

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2)
        for s in (self._udp, self._tcp):
            if s:
                s.close()

    def serve_forever(self) -> None:
        self.start()
        try:
            while not self._stop.wait(0.5):
                pass
        finally:
            self.stop()

    # ---- UDP ----

    def _udp_loop(self) -> None:
        sock = self._udp
        assert sock is not None
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(2048)
            except TimeoutError:
                continue
            except OSError:
                return
            reply = self.state.handle(data, addr)
            if reply is not None:
                try:
                    sock.sendto(reply, addr)
                except OSError:
                    pass

    # ---- TCP ----

    def _tcp_loop(self) -> None:
        srv = self._tcp
        assert srv is not None
        while not self._stop.is_set():
            try:
                conn, addr = srv.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            if not self._admit(addr[0]):
                conn.close()
                continue
            threading.Thread(target=self._tcp_conn, args=(conn, addr[0]), daemon=True).start()

    def _admit(self, ip: str) -> bool:
        with self._lock:
            if sum(self._conns.values()) >= TCP_MAX_CONN or self._conns.get(ip, 0) >= TCP_MAX_CONN_PER_IP:
                return False
            self._conns[ip] = self._conns.get(ip, 0) + 1
            return True

    def _release(self, ip: str) -> None:
        with self._lock:
            self._conns[ip] -= 1
            if not self._conns[ip]:
                del self._conns[ip]

    def _tcp_conn(self, conn: socket.socket, ip: str) -> None:
        try:
            conn.settimeout(5)
            raw = _recv_exact(conn, TCP_HEADER.size)
            magic, mode, ms = TCP_HEADER.unpack(raw)
            if magic != TCP_MAGIC or mode not in (TCP_UP, TCP_DOWN):
                return
            ms = min(ms, TCP_MAX_MS)
            if mode == TCP_DOWN:
                _send_for(conn, ms / 1000.0, self._stop)
            else:
                total = _drain(conn, ms / 1000.0 + 5.0, self._stop)
                conn.sendall(struct.pack("<Q", total))
        except (OSError, struct.error, ConnectionError):
            pass
        finally:
            conn.close()
            self._release(ip)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def _send_for(conn: socket.socket, secs: float, stop: threading.Event) -> None:
    buf = b"\0" * TCP_CHUNK
    end = time.monotonic() + secs
    conn.settimeout(5)
    while time.monotonic() < end and not stop.is_set():
        conn.sendall(buf)


def _drain(conn: socket.socket, max_secs: float, stop: threading.Event) -> int:
    """Read until the client half-closes. Returns bytes received."""
    total = 0
    end = time.monotonic() + max_secs
    while time.monotonic() < end and not stop.is_set():
        chunk = conn.recv(TCP_CHUNK)
        if not chunk:
            break
        total += len(chunk)
    return total
