"""Mathis model: rate <= (MSS / RTT) * C / sqrt(p), with C = sqrt(3/2) ~ 1.22."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

C = math.sqrt(1.5)
DEFAULT_MSS = 1448
PAIR_WINDOW_S = 300.0
LOSS_KINDS = ("udp", "icmp")


def mathis_mbps(mss_bytes: float, rtt_ms: float, loss_frac: float) -> float | None:
    """Predicted TCP throughput in Mbit/s. None when loss is 0 (unbounded)."""
    if loss_frac <= 0 or rtt_ms <= 0:
        return None
    bps = (mss_bytes * 8.0) / (rtt_ms / 1000.0) * C / math.sqrt(loss_frac)
    return bps / 1e6


def curve(mss_bytes: float, rtt_ms: float, losses: Sequence[float]) -> list[tuple[float, float]]:
    """(loss fraction, Mbit/s) points for a range of loss values."""
    pts = []
    for p in losses:
        v = mathis_mbps(mss_bytes, rtt_ms, p)
        if v is not None:
            pts.append((p, v))
    return pts


@dataclass(frozen=True, slots=True)
class Pair:
    profile: str
    ts: float
    loss_frac: float
    rtt_ms: float
    measured_mbps: float
    predicted_mbps: float

    @property
    def ratio(self) -> float:
        return self.measured_mbps / self.predicted_mbps


def pair_samples(
    tput_rows: Iterable[Mapping],
    loss_rows: Sequence[Mapping],
    window_s: float = PAIR_WINDOW_S,
    mss: float = DEFAULT_MSS,
) -> list[Pair]:
    """Match each throughput sample to the nearest loss sample in time.

    Both inputs are row dicts with `profile` and `ts`. Loss rows need
    `kind`, `loss_pct` and an RTT. Pairs with zero loss are skipped.
    """
    cands: dict[str, list[Mapping]] = {}
    for lr in loss_rows:
        if lr["kind"] in LOSS_KINDS and lr["loss_pct"] is not None and _rtt(lr) is not None:
            cands.setdefault(lr["profile"], []).append(lr)
    pairs: list[Pair] = []
    for tr in tput_rows:
        near = [c for c in cands.get(tr["profile"], []) if abs(c["ts"] - tr["ts"]) <= window_s]
        if not near or not tr["mbps"]:
            continue
        best = min(near, key=lambda c: abs(c["ts"] - tr["ts"]))
        frac = best["loss_pct"] / 100.0
        rtt = _rtt(best)
        pred = mathis_mbps(mss, rtt, frac)
        if pred is None:
            continue
        pairs.append(Pair(tr["profile"], tr["ts"], frac, rtt, tr["mbps"], pred))
    return pairs


def _rtt(row: Mapping) -> float | None:
    for key in ("rtt_p50", "rtt_mean"):
        v = row[key] if key in row.keys() else None
        if v:
            return float(v)
    return None
