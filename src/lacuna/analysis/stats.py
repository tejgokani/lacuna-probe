"""Percentiles, RFC 3550 jitter, loss sequences and burst statistics."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

# A loss sequence is a list of 0/1 ints, one per packet in send order.
# 1 means the packet was lost, 0 means it came back.


def percentile(values: Sequence[float], q: float) -> float:
    """Linear interpolation percentile, q in [0, 100]. Needs a non-empty input."""
    if not values:
        raise ValueError("percentile of empty sequence")
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = min(lo + 1, len(xs) - 1)
    return float(xs[lo] + (xs[hi] - xs[lo]) * (pos - lo))


def rtt_summary(rtts_ms: Sequence[float]) -> dict[str, float | None]:
    """min, mean, p50, p95, p99, max. All None when there are no samples."""
    keys = ("rtt_min", "rtt_mean", "rtt_p50", "rtt_p95", "rtt_p99", "rtt_max")
    if not rtts_ms:
        return dict.fromkeys(keys)
    return {
        "rtt_min": float(min(rtts_ms)),
        "rtt_mean": sum(rtts_ms) / len(rtts_ms),
        "rtt_p50": percentile(rtts_ms, 50),
        "rtt_p95": percentile(rtts_ms, 95),
        "rtt_p99": percentile(rtts_ms, 99),
        "rtt_max": float(max(rtts_ms)),
    }


def rfc3550_jitter(transit_ms: Sequence[float]) -> float:
    """Interarrival jitter, J += (|D| - J) / 16, over consecutive samples.

    With only a sender clock, RTT stands in for transit time. The result
    measures RTT variation between consecutive replies.
    """
    j = 0.0
    for prev, cur in zip(transit_ms, transit_ms[1:], strict=False):
        j += (abs(cur - prev) - j) / 16.0
    return j


def loss_pct(sent: int, recv: int) -> float:
    return 0.0 if sent <= 0 else 100.0 * (sent - recv) / sent


def bits_to_hex(bits: Sequence[int]) -> str:
    """Pack a loss sequence MSB first, padded with zeros to a whole byte."""
    out = bytearray((len(bits) + 7) // 8)
    for i, b in enumerate(bits):
        if b:
            out[i // 8] |= 0x80 >> (i % 8)
    return out.hex()


def hex_to_bits(hexstr: str, n: int) -> list[int]:
    raw = bytes.fromhex(hexstr)
    return [(raw[i // 8] >> (7 - i % 8)) & 1 if i // 8 < len(raw) else 0 for i in range(n)]


def burst_lengths(bits: Sequence[int]) -> list[int]:
    """Lengths of runs of consecutive lost packets."""
    runs: list[int] = []
    cur = 0
    for b in bits:
        if b:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    return runs


def burst_histogram(runs: Sequence[int]) -> dict[int, int]:
    return dict(sorted(Counter(runs).items()))


def random_burst_expectation(loss_frac: float, n_bursts: int, max_len: int) -> list[float]:
    """Expected burst counts for lengths 1..max_len if loss were independent.

    Run length is geometric: P(L = k) = p^(k-1) (1 - p). The total is scaled
    to the number of bursts actually seen.
    """
    p = min(max(loss_frac, 0.0), 0.999999)
    return [n_bursts * p ** (k - 1) * (1 - p) for k in range(1, max_len + 1)]


def runs_test_z(bits: Sequence[int]) -> float | None:
    """Wald-Wolfowitz runs test z score. Negative z means fewer runs than
    random, which is what bursty loss looks like. None when undefined."""
    n = len(bits)
    n1 = sum(1 for b in bits if b)
    n2 = n - n1
    if n1 == 0 or n2 == 0 or n < 3:
        return None
    runs = 1 + sum(1 for a, b in zip(bits, bits[1:], strict=False) if a != b)
    mu = 2.0 * n1 * n2 / n + 1.0
    var = (mu - 1.0) * (mu - 2.0) / (n - 1.0)
    if var <= 0:
        return None
    return (runs - mu) / math.sqrt(var)


def median(values: Sequence[float]) -> float | None:
    return percentile(values, 50) if values else None
