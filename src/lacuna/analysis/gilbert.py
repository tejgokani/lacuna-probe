"""Gilbert-Elliott (two state Markov) loss model: fit and simulate.

States: good (packet delivered) and bad (packet lost).
    p = P(good -> bad)    r = P(bad -> good)
    mean burst = 1 / r    stationary loss = p / (p + r)
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

MIN_RELIABLE = 200


@dataclass(frozen=True, slots=True)
class GEFit:
    p: float | None
    r: float | None
    n: int
    losses: int
    note: str = ""

    @property
    def mean_burst(self) -> float | None:
        return None if not self.r else 1.0 / self.r

    @property
    def stationary_loss(self) -> float | None:
        if self.p is None or self.r is None or (self.p + self.r) == 0:
            return None
        return self.p / (self.p + self.r)

    @property
    def reliable(self) -> bool:
        return self.n >= MIN_RELIABLE and self.note == ""


def count_transitions(bits: Sequence[int]) -> tuple[int, int, int, int]:
    """Return (good_prev, good_to_bad, bad_prev, bad_to_good)."""
    gp = gb = bp = bg = 0
    for a, b in zip(bits, bits[1:], strict=False):
        if a:
            bp += 1
            bg += 0 if b else 1
        else:
            gp += 1
            gb += 1 if b else 0
    return gp, gb, bp, bg


def fit_counts(gp: int, gb: int, bp: int, bg: int, n: int, losses: int) -> GEFit:
    p = gb / gp if gp else None
    r = bg / bp if bp else None
    notes = []
    if n < MIN_RELIABLE:
        notes.append(f"sample too small ({n} < {MIN_RELIABLE} packets)")
    if losses == 0:
        notes.append("no losses, r undefined")
    elif r is None:
        notes.append("loss seen but no recovery, r undefined")
    return GEFit(p, r, n, losses, "; ".join(notes))


def fit(bits: Sequence[int]) -> GEFit:
    """Maximum likelihood fit from one loss sequence."""
    gp, gb, bp, bg = count_transitions(bits)
    return fit_counts(gp, gb, bp, bg, len(bits), sum(bits))


def fit_many(sequences: Sequence[Sequence[int]]) -> GEFit:
    """Pool transition counts over several runs without joining their ends."""
    tot = [0, 0, 0, 0]
    n = losses = 0
    for bits in sequences:
        for i, c in enumerate(count_transitions(bits)):
            tot[i] += c
        n += len(bits)
        losses += sum(bits)
    return fit_counts(tot[0], tot[1], tot[2], tot[3], n, losses)


def params_for(loss: float, mean_burst: float) -> tuple[float, float]:
    """(p, r) that give stationary `loss` (fraction) and the given mean burst."""
    if not 0 <= loss < 1:
        raise ValueError("loss must be in [0, 1)")
    if mean_burst < 1:
        raise ValueError("mean burst must be at least 1")
    r = 1.0 / mean_burst
    p = r * loss / (1.0 - loss) if loss > 0 else 0.0
    return min(p, 1.0), r


def simulate(p: float, r: float, n: int, rng: random.Random) -> list[int]:
    """Draw a loss sequence, starting from the stationary distribution."""
    pi_bad = p / (p + r) if (p + r) > 0 else 0.0
    bad = rng.random() < pi_bad
    out: list[int] = []
    for _ in range(n):
        out.append(1 if bad else 0)
        bad = (rng.random() >= r) if bad else (rng.random() < p)
    return out
