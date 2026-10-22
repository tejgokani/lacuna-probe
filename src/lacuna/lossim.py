"""Loss simulator for the offline demo and the tests.

UdpLossSim sits between a client and a Lacuna server and drops PROBE and
ECHO datagrams with a chosen model. Control packets pass untouched so the
handshake and stats exchange stay reliable. TcpDelayProxy adds one-way
delay to a TCP stream without limiting its rate.
"""

from __future__ import annotations

import heapq
import queue
import random
import select
import socket
import threading
import time
from dataclasses import dataclass, field

from lacuna import protocol as P
from lacuna.analysis import gilbert


class LossModel:
    def drop(self) -> bool:
        raise NotImplementedError


class NoLoss(LossModel):
    def drop(self) -> bool:
        return False


class UniformLoss(LossModel):
    def __init__(self, p: float, seed: int = 0):
        self.p = p
        self.rng = random.Random(seed)

    def drop(self) -> bool:
        return self.rng.random() < self.p


class GilbertLoss(LossModel):
    """Two state chain. Packets are lost while in the bad state."""

    def __init__(self, loss: float, mean_burst: float, seed: int = 0):
        self.p, self.r = gilbert.params_for(loss, mean_burst)
        self.rng = random.Random(seed)
        self.bad = self.rng.random() < (loss if loss > 0 else 0.0)

    def drop(self) -> bool:
        lost = self.bad
        self.bad = (self.rng.random() >= self.r) if self.bad else (self.rng.random() < self.p)
        return lost


def split_loss(total: float) -> tuple[float, float]:
    """Split a round-trip loss fraction into (uplink, downlink) so that
    (1 - up)(1 - down) = 1 - total, with uplink carrying 2/3 of the log loss."""
    keep = 1.0 - total
    return 1.0 - keep ** (2 / 3), 1.0 - keep ** (1 / 3)


@dataclass(slots=True)
class _Client:
    upstream: socket.socket
    addr: tuple[str, int]


@dataclass(order=True)
class _Pending:
    due: float
    n: int
    sock: socket.socket = field(compare=False)
    data: bytes = field(compare=False)
    dest: tuple[str, int] = field(compare=False)


class UdpLossSim:
    """Forward UDP between clients on `listen` and a server at `target`."""

    def __init__(
        self, listen: tuple[str, int], target: tuple[str, int],
        up: LossModel | None = None, down: LossModel | None = None, delay_ms: float = 0.0,
    ):
        self.listen = listen
        self.target = target
        self.up = up or NoLoss()
        self.down = down or NoLoss()
        self.delay = delay_ms / 1000.0
        self.dropped_up = 0
        self.dropped_down = 0
        self.seen_up = 0
        self.seen_down = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._clients: dict[tuple[str, int], _Client] = {}
        self._heap: list[_Pending] = []
        self._n = 0

    def start(self) -> UdpLossSim:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(self.listen)
        self._thread = threading.Thread(target=self._loop, daemon=True, name="lossim")
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        for c in self._clients.values():
            c.upstream.close()
        if self._sock:
            self._sock.close()

    def _loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            socks = [self._sock] + [c.upstream for c in self._clients.values()]
            timeout = 0.05
            if self._heap:
                timeout = max(0.0, min(timeout, self._heap[0].due - time.monotonic()))
            ready, _, _ = select.select(socks, [], [], timeout)
            for s in ready:
                if s is self._sock:
                    self._from_client()
                else:
                    self._from_server(s)
            self._flush()

    def _is_data(self, data: bytes) -> bool:
        return len(data) > 5 and data[:4] == P.MAGIC and data[5] in (P.PROBE, P.ECHO)

    def _from_client(self) -> None:
        data, addr = self._sock.recvfrom(2048)  # type: ignore[union-attr]
        if self._is_data(data):
            self.seen_up += 1
            if self.up.drop():
                self.dropped_up += 1
                return
        cl = self._clients.get(addr)
        if cl is None:
            up = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            up.bind(("127.0.0.1", 0))
            cl = self._clients[addr] = _Client(up, addr)
        self._schedule(cl.upstream, data, self.target)

    def _from_server(self, sock: socket.socket) -> None:
        try:
            data, _ = sock.recvfrom(2048)
        except OSError:
            return
        cl = next(c for c in self._clients.values() if c.upstream is sock)
        if self._is_data(data):
            self.seen_down += 1
            if self.down.drop():
                self.dropped_down += 1
                return
        self._schedule(self._sock, data, cl.addr)  # type: ignore[arg-type]

    def _schedule(self, sock: socket.socket, data: bytes, dest: tuple[str, int]) -> None:
        if self.delay <= 0:
            sock.sendto(data, dest)
            return
        self._n += 1
        heapq.heappush(self._heap, _Pending(time.monotonic() + self.delay, self._n, sock, data, dest))

    def _flush(self) -> None:
        now = time.monotonic()
        while self._heap and self._heap[0].due <= now:
            p = heapq.heappop(self._heap)
            try:
                p.sock.sendto(p.data, p.dest)
            except OSError:
                pass


class TcpDelayProxy:
    """TCP relay that delays each chunk by `delay_ms` in each direction and
    can cap the rate. A bounded queue gives backpressure, so memory stays flat."""

    def __init__(
        self, listen: tuple[str, int], target: tuple[str, int], delay_ms: float = 10.0,
        rate_mbps: float | None = None,
    ):
        self.listen = listen
        self.target = target
        self.delay = delay_ms / 1000.0
        self.rate_bps = rate_mbps * 1e6 if rate_mbps else None
        self._stop = threading.Event()
        self._srv: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> TcpDelayProxy:
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(self.listen)
        self._srv.listen(16)
        self._srv.settimeout(0.25)
        self._thread = threading.Thread(target=self._accept, daemon=True, name="tcpdelay")
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._srv:
            self._srv.close()

    def _accept(self) -> None:
        assert self._srv is not None
        while not self._stop.is_set():
            try:
                client, _ = self._srv.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                upstream = socket.create_connection(self.target, timeout=5)
            except OSError:
                client.close()
                continue
            done = threading.Semaphore(0)
            for src, dst in ((client, upstream), (upstream, client)):
                self._pipe(src, dst, done)
            threading.Thread(target=_close_after, args=(done, client, upstream), daemon=True).start()

    def _pipe(self, src: socket.socket, dst: socket.socket, done: threading.Semaphore) -> None:
        q: queue.Queue[tuple[float, bytes] | None] = queue.Queue(maxsize=8 if self.rate_bps else 256)
        threading.Thread(target=self._reader, args=(src, q), daemon=True).start()
        threading.Thread(target=self._writer, args=(dst, q, done), daemon=True).start()

    def _reader(self, src: socket.socket, q: queue.Queue) -> None:
        try:
            while not self._stop.is_set():
                chunk = src.recv(65536)
                if not chunk:
                    break
                q.put((time.monotonic() + self.delay, chunk))
        except OSError:
            pass
        q.put(None)

    def _writer(self, dst: socket.socket, q: queue.Queue, done: threading.Semaphore) -> None:
        free_at = time.monotonic()  # when the shaped link is idle again
        try:
            while True:
                item = q.get()
                if item is None:
                    break
                due, chunk = item
                if self.rate_bps:
                    due = max(due, free_at)
                    free_at = due + len(chunk) * 8 / self.rate_bps
                wait = due - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                dst.sendall(chunk)
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass
        except OSError:
            pass
        finally:
            done.release()


def _close_after(done: threading.Semaphore, *socks: socket.socket) -> None:
    for _ in socks:
        done.acquire()
    for s in socks:
        s.close()
