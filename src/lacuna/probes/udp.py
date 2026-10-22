"""Sequence-numbered UDP prober. Splits loss into uplink and downlink."""

from __future__ import annotations

import random
import socket
import time
from dataclasses import dataclass, field

from lacuna import protocol as P
from lacuna.analysis import gilbert, stats
from lacuna.db import LossSample
from lacuna.probes import resolve_ipv4


class UdpProbeError(RuntimeError):
    pass


@dataclass(slots=True)
class _Tracker:
    """Receive-side bookkeeping for one run."""

    count: int
    rtts: dict[int, float] = field(default_factory=dict)  # seq -> rtt ms
    arrival: list[float] = field(default_factory=list)  # rtt ms in arrival order
    dup: int = 0
    reordered: int = 0
    highest: int = -1

    def on_echo(self, seq: int, rtt_ms: float) -> None:
        if not 0 <= seq < self.count:
            return
        if seq in self.rtts:
            self.dup += 1
            return
        if seq < self.highest:
            self.reordered += 1
        self.highest = max(self.highest, seq)
        self.rtts[seq] = rtt_ms
        self.arrival.append(rtt_ms)


def _handshake(sock: socket.socket, session: int, tries: int = 5, wait: float = 1.0) -> bytes:
    hello = P.build_hello(session)
    for _ in range(tries):
        sock.send(hello)
        end = time.monotonic() + wait
        while (left := end - time.monotonic()) > 0:
            sock.settimeout(left)
            try:
                data = sock.recv(2048)
            except TimeoutError:
                continue
            except ConnectionRefusedError:
                time.sleep(min(left, 0.05))
                continue
            try:
                h = P.unpack_header(data)
            except P.ProtocolError:
                continue
            if h.type == P.HELLO_ACK and h.session == session and len(data) >= P.MIN_PROBE:
                return P.probe_cookie(data)
    raise UdpProbeError("no HELLO_ACK. Is `lacuna server` running and is the UDP port open?")


def _feed(sock: socket.socket, session: int, trk: _Tracker, timeout: float) -> P.Stats | None:
    """Wait up to `timeout` for one datagram. Echoes go to the tracker.
    Returns parsed stats if that is what arrived."""
    sock.settimeout(max(timeout, 0.0005))
    try:
        data = sock.recv(2048)
    except (TimeoutError, BlockingIOError):
        return None
    except ConnectionRefusedError:  # ICMP port unreachable on a connected socket
        time.sleep(min(timeout, 0.01))
        return None
    t_rx = P.now_ns()
    try:
        h = P.unpack_header(data)
    except P.ProtocolError:
        return None
    if h.session != session:
        return None
    if h.type == P.ECHO:
        trk.on_echo(h.seq, (t_rx - h.t_send_ns) / 1e6)
    elif h.type == P.STATS:
        try:
            return P.parse_stats(data)
        except P.ProtocolError:
            return None
    return None


def _send_all(sock, session, cookie, trk, count, pps, size) -> None:
    start = time.monotonic()
    for seq in range(count):
        due = start + seq / pps
        while (left := due - time.monotonic()) > 0:
            _feed(sock, session, trk, left)
        sock.send(P.build_probe(session, seq, P.now_ns(), cookie, size))


def _fetch_stats(sock, session, cookie, trk, count) -> P.Stats | None:
    req = P.build_stats_req(session, cookie, P.stats_req_size(count))
    for _ in range(5):
        sock.send(req)
        end = time.monotonic() + 0.6
        while (left := end - time.monotonic()) > 0:
            st = _feed(sock, session, trk, left)
            if st is not None:
                return st
    return None


def run_udp(
    host: str, port: int, count: int = 500, pps: float = 20, size: int = 200,
    grace: float = 1.5, rng: random.Random | None = None,
    hello_tries: int = 5, hello_wait: float = 1.0,
) -> LossSample:
    """Send `count` probes at `pps`, wait `grace` s for stragglers, fetch server stats."""
    if not 1 <= count <= P.MAX_UDP_COUNT:
        raise UdpProbeError(f"count must be between 1 and {P.MAX_UDP_COUNT}")
    if pps <= 0:
        raise UdpProbeError("pps must be positive")
    session = (rng or random.SystemRandom()).getrandbits(32)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((resolve_ipv4(host), port))
        cookie = _handshake(sock, session, hello_tries, hello_wait)
        trk = _Tracker(count)
        _send_all(sock, session, cookie, trk, count, pps, size)
        end = time.monotonic() + grace
        while (left := end - time.monotonic()) > 0 and len(trk.rtts) < count:
            _feed(sock, session, trk, left)
        srv = _fetch_stats(sock, session, cookie, trk, count)
    finally:
        sock.close()
    return build_sample(trk, srv, count)


def build_sample(trk: _Tracker, srv: P.Stats | None, count: int) -> LossSample:
    bits = [0 if i in trk.rtts else 1 for i in range(count)]
    recv = count - sum(bits)
    up = down = None
    if srv is not None:
        up = stats.loss_pct(count, srv.rx_count)
        down = stats.loss_pct(srv.rx_count, recv) if srv.rx_count else None
    runs = stats.burst_lengths(bits)
    fit = gilbert.fit(bits)
    ordered = [trk.rtts[i] for i in sorted(trk.rtts)]
    return LossSample(
        sent=count,
        recv=recv,
        loss_pct=stats.loss_pct(count, recv),
        up_loss_pct=up,
        down_loss_pct=down,
        jitter_ms=stats.rfc3550_jitter(ordered) if len(ordered) > 1 else None,
        dup=trk.dup,
        reordered=trk.reordered,
        max_burst=max(runs) if runs else 0,
        ge_p=fit.p,
        ge_r=fit.r,
        loss_bits_hex=stats.bits_to_hex(bits),
        **stats.rtt_summary(ordered),
    )
