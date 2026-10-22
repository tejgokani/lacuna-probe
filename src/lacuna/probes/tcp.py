"""TCP connect prober: time a handshake, count refusals and timeouts as loss."""

from __future__ import annotations

import socket
import time

from lacuna.analysis import stats
from lacuna.db import LossSample
from lacuna.probes import resolve_ipv4


def run_tcp(
    host: str, port: int, count: int = 50, interval: float = 0.5, timeout: float = 2.0,
    stop=None,
) -> LossSample:
    addr = resolve_ipv4(host)
    rtts: list[float] = []
    bits: list[int] = []
    for i in range(count):
        if stop is not None and stop.is_set():
            break
        t0 = time.perf_counter()
        try:
            with socket.create_connection((addr, port), timeout=timeout):
                rtts.append((time.perf_counter() - t0) * 1000.0)
            bits.append(0)
        except OSError:
            bits.append(1)
        if i < count - 1:
            time.sleep(max(0.0, interval - (time.perf_counter() - t0)))
    sent = len(bits)
    recv = sent - sum(bits)
    runs = stats.burst_lengths(bits)
    return LossSample(
        sent=sent,
        recv=recv,
        loss_pct=stats.loss_pct(sent, recv),
        jitter_ms=stats.rfc3550_jitter(rtts) if len(rtts) > 1 else None,
        max_burst=max(runs) if runs else 0,
        loss_bits_hex=stats.bits_to_hex(bits),
        **stats.rtt_summary(rtts),
    )
