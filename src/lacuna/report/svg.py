"""Tiny inline SVG chart helpers.

Charts carry no colors of their own. They use CSS classes (s0..s5 for
strokes and fills, f0..f5 for text) that the page defines, so one chart
works in light and dark mode. No xmlns is written: the markup lives inline
in HTML, where the parser does not need it.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from html import escape

W = 760
ML, MR, MT, MB = 58, 124, 34, 44
MAX_MARKERS = 150


@dataclass(slots=True)
class Series:
    name: str
    idx: int  # color slot 0..5
    points: list[tuple[float, float]]
    tips: list[str] | None = None
    dash: bool = False


@dataclass(slots=True)
class ScatterGroup:
    name: str
    idx: int
    points: list[tuple[float, float, str]] = field(default_factory=list)  # x, y, tooltip
    curve: list[tuple[float, float]] = field(default_factory=list)


class Scale:
    def __init__(self, lo: float, hi: float, a: float, b: float, log: bool = False):
        self.log = log
        self.lo, self.hi = (math.log10(lo), math.log10(hi)) if log else (lo, hi)
        if self.hi == self.lo:
            self.hi = self.lo + 1.0
        self.a, self.b = a, b

    def __call__(self, v: float) -> float:
        if self.log:
            v = math.log10(max(v, 1e-12))
        return self.a + (v - self.lo) / (self.hi - self.lo) * (self.b - self.a)


# ---- numbers and ticks ----

def fmt_num(v: float) -> str:
    if v == 0:
        return "0"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 100:
        return f"{v:.0f}"
    if abs(v) >= 10:
        return f"{v:.1f}".rstrip("0").rstrip(".")
    return f"{v:.2f}".rstrip("0").rstrip(".")


def nice_range(lo: float, hi: float, n: int = 5) -> tuple[float, float, list[float]]:
    if hi <= lo:
        hi = lo + 1.0
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), 10 * mag)
    lo2 = math.floor(lo / step + 1e-9) * step
    hi2 = math.ceil(hi / step - 1e-9) * step
    count = int(round((hi2 - lo2) / step))
    return lo2, hi2, [round(lo2 + i * step, 10) for i in range(count + 1)]


def log_ticks(lo: float, hi: float) -> tuple[float, float, list[float]]:
    a, b = math.floor(math.log10(lo)), math.ceil(math.log10(hi))
    if a == b:
        b += 1
    return 10.0**a, 10.0**b, [10.0**k for k in range(a, b + 1)]


_TSTEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600,
           43200, 86400, 172800, 604800]


def time_ticks(t0: float, t1: float, n: int = 6) -> tuple[float, float, list[tuple[float, str]]]:
    """Ticks on local-time boundaries. Returns (t0, t1, [(t, label)])."""
    if t1 - t0 < 1:
        t0, t1 = t0 - 30, t1 + 30
    span = t1 - t0
    step = next((s for s in _TSTEPS if span / s <= n), _TSTEPS[-1])
    off = time.localtime(t0).tm_gmtoff or 0
    fmt = "%H:%M:%S" if span <= 120 else "%H:%M" if span <= 2 * 86400 else "%m-%d"
    if step >= 86400:
        fmt = "%m-%d"
    t = math.ceil((t0 + off) / step) * step - off
    ticks = []
    while t <= t1:
        ticks.append((t, time.strftime(fmt, time.localtime(t))))
        t += step
    return t0, t1, ticks


# ---- shared drawing ----

def _open(title: str, h: int) -> str:
    return f'<svg class="chart" viewBox="0 0 {W} {h}" role="img" aria-label="{escape(title)}">'


def _title(title: str) -> str:
    return f'<text class="ct" x="{ML}" y="18">{escape(title)}</text>'


def _frame(
    xs: Scale, ys: Scale, xticks: Sequence[tuple[float, str]], yticks: Sequence[tuple[float, str]],
    xlabel: str, ylabel: str, h: int,
) -> str:
    out = []
    for v, label in yticks:
        y = ys(v)
        out.append(f'<line class="gr" x1="{ML}" x2="{W - MR}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="tk" x="{ML - 6}" y="{y + 4:.1f}" text-anchor="end">{escape(label)}</text>')
    for v, label in xticks:
        x = xs(v)
        out.append(f'<line class="gr" x1="{x:.1f}" x2="{x:.1f}" y1="{MT}" y2="{h - MB}"/>')
        out.append(f'<text class="tk" x="{x:.1f}" y="{h - MB + 15}" text-anchor="middle">{escape(label)}</text>')
    out.append(f'<line class="ax" x1="{ML}" x2="{W - MR}" y1="{h - MB}" y2="{h - MB}"/>')
    out.append(f'<line class="ax" x1="{ML}" x2="{ML}" y1="{MT}" y2="{h - MB}"/>')
    mid = (MT + h - MB) / 2
    out.append(f'<text class="lb" x="{(ML + W - MR) / 2:.0f}" y="{h - 5}" text-anchor="middle">{escape(xlabel)}</text>')
    out.append(f'<text class="lb" transform="rotate(-90 13 {mid:.0f})" x="13" y="{mid:.0f}" text-anchor="middle">{escape(ylabel)}</text>')
    return "".join(out)


def _clip(text: str, n: int = 19) -> str:
    return text if len(text) <= n else text[: n - 1] + "..."


def _direct_labels(items: list[tuple[float, str, int]]) -> str:
    """Place (y, text, idx) labels at the right edge, nudged apart."""
    out = []
    last = -1e9
    for y, text, idx in sorted(items):
        y = max(y, last + 12)
        last = y
        out.append(f'<text class="dl f{idx % 6}" x="{W - MR + 6}" y="{y + 4:.1f}">{escape(_clip(text))}</text>')
    return "".join(out)


def _empty(title: str, msg: str, h: int = 90) -> str:
    return (f'{_open(title, h)}{_title(title)}<text class="tk" x="{ML}" y="{h / 2 + 10:.0f}">'
            f"{escape(msg)}</text></svg>")


def _ylabels(ticks: list[float]) -> list[tuple[float, str]]:
    return [(t, fmt_num(t)) for t in ticks]


# ---- charts ----

def line_chart(
    title: str, series: Sequence[Series], xlabel: str, ylabel: str,
    xdomain: tuple[float, float] | None = None, ymin: float | None = 0.0, h: int = 210,
    time_axis: bool = True,
) -> str:
    pts = [p for s in series for p in s.points]
    if not pts:
        return _empty(title, "No data in this range.")
    x0, x1 = xdomain or (min(p[0] for p in pts), max(p[0] for p in pts))
    xlo, xhi, xt = time_ticks(x0, x1) if time_axis else (x0, x1, [(t, fmt_num(t)) for t in nice_range(x0, x1)[2]])
    ylo, yhi, yt = nice_range(ymin if ymin is not None else min(p[1] for p in pts),
                              max(p[1] for p in pts) * 1.05 or 1.0)
    xs, ys = Scale(xlo, xhi, ML, W - MR), Scale(ylo, yhi, h - MB, MT)
    out = [_open(title, h), _title(title), _frame(xs, ys, xt, _ylabels(yt), xlabel, ylabel, h)]
    labels = []
    for s in series:
        if not s.points:
            continue
        coords = " ".join(f"{xs(x):.1f},{ys(y):.1f}" for x, y in s.points)
        dash = ' stroke-dasharray="5 4"' if s.dash else ""
        out.append(f'<polyline class="ln s{s.idx % 6}" fill="none"{dash} points="{coords}"/>')
        if len(s.points) <= MAX_MARKERS:
            out.append(_markers(s, xs, ys))
        labels.append((ys(s.points[-1][1]), s.name, s.idx))
    out.append(_direct_labels(labels))
    out.append("</svg>")
    return "".join(out)


def _markers(s: Series, xs: Scale, ys: Scale) -> str:
    parts = []
    for i, (x, y) in enumerate(s.points):
        tip = s.tips[i] if s.tips else f"{s.name}: {fmt_num(y)}"
        parts.append(f'<circle class="mk f{s.idx % 6}" cx="{xs(x):.1f}" cy="{ys(y):.1f}" r="3">'
                     f"<title>{escape(tip)}</title></circle>")
    return "".join(parts)


def bar_chart(
    title: str, cats: Sequence[str], cat_idx: Sequence[int],
    series: Sequence[tuple[str, float, Sequence[float | None]]],
    ylabel: str, fmt: Callable[[float], str] = fmt_num, h: int = 236,
) -> str:
    """Grouped bars. Color comes from the category (cat_idx), the series
    differ by opacity and by a text label under each bar."""
    vals = [v for _, _, vs in series for v in vs if v is not None]
    if not cats or not vals:
        return _empty(title, "No data in this range.")
    _, yhi, yt = nice_range(0.0, max(vals) * 1.15 or 1.0)
    ys = Scale(0, yhi, h - MB, MT)
    out = [_open(title, h), _title(title),
           _frame(Scale(0, 1, ML, W - MR), ys, [], _ylabels(yt), "", ylabel, h)]
    gw = (W - MR - ML) / len(cats)
    bw = gw * 0.7 / len(series)
    for ci, cat in enumerate(cats):
        gx = ML + ci * gw + gw * 0.15
        for si, (name, opacity, vs) in enumerate(series):
            v = vs[ci]
            if v is None:
                continue
            x, y = gx + si * bw, ys(v)
            out.append(f'<rect class="bar s{cat_idx[ci] % 6}" fill-opacity="{opacity}" x="{x:.1f}" y="{y:.1f}" '
                       f'width="{bw - 2:.1f}" height="{max(h - MB - y, 0):.1f}">'
                       f"<title>{escape(f'{cat}, {name}: {fmt(v)}')}</title></rect>")
            out.append(f'<text class="tk" x="{x + bw / 2 - 1:.1f}" y="{y - 4:.1f}" text-anchor="middle">{escape(fmt(v))}</text>')
            out.append(f'<text class="tk" x="{x + bw / 2 - 1:.1f}" y="{h - MB + 14}" text-anchor="middle">{escape(name)}</text>')
        out.append(f'<text class="lb" x="{ML + ci * gw + gw / 2:.1f}" y="{h - 8}" text-anchor="middle">{escape(_clip(cat, 28))}</text>')
    return "".join(out) + "</svg>"


def hist_chart(
    title: str, observed: dict[int, int], expected: Sequence[float], idx: int,
    xlabel: str = "burst length (consecutive lost packets)", ylabel: str = "bursts", h: int = 210,
) -> str:
    if not observed:
        return _empty(title, "No loss bursts recorded.")
    kmax = max(max(observed), len(expected), 3)
    top = max(max(observed.values()), max(expected, default=0)) * 1.15 or 1.0
    _, yhi, yt = nice_range(0.0, top)
    ys = Scale(0, yhi, h - MB, MT)
    xs = Scale(0.5, kmax + 0.5, ML, W - MR)
    xt = [(k, str(k)) for k in range(1, kmax + 1)]
    out = [_open(title, h), _title(title), _frame(xs, ys, xt, _ylabels(yt), xlabel, ylabel, h)]
    bw = (xs(2) - xs(1)) * 0.6
    for k, n in observed.items():
        y = ys(n)
        out.append(f'<rect class="bar s{idx % 6}" x="{xs(k) - bw / 2:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                   f'height="{h - MB - y:.1f}"><title>{n} bursts of length {k}</title></rect>')
    if expected:
        pts = [(k + 1, e) for k, e in enumerate(expected)]
        coords = " ".join(f"{xs(k):.1f},{ys(e):.1f}" for k, e in pts)
        out.append(f'<polyline class="ln exp" fill="none" stroke-dasharray="4 3" points="{coords}"/>')
        for k, e in pts:
            out.append(f'<circle class="mk exp" cx="{xs(k):.1f}" cy="{ys(e):.1f}" r="3">'
                       f"<title>random loss would give {e:.1f} bursts of length {k}</title></circle>")
    out.append(f'<text class="dl f{idx % 6}" x="{W - MR + 6}" y="{MT + 14}">observed</text>')
    out.append(f'<text class="dl expt" x="{W - MR + 6}" y="{MT + 30}">if random</text>')
    return "".join(out) + "</svg>"


def scatter_chart(
    title: str, groups: Sequence[ScatterGroup], xlabel: str, ylabel: str, h: int = 280,
) -> str:
    pts = [(x, y) for g in groups for x, y, _ in g.points] + [p for g in groups for p in g.curve]
    pts = [p for p in pts if p[0] > 0 and p[1] > 0]
    if not any(g.points for g in groups) or not pts:
        return _empty(title, "No paired samples to plot.")
    xlo, xhi, xt = log_ticks(min(p[0] for p in pts), max(p[0] for p in pts))
    ylo, yhi, yt = log_ticks(min(p[1] for p in pts), max(p[1] for p in pts))
    xs, ys = Scale(xlo, xhi, ML, W - MR, log=True), Scale(ylo, yhi, h - MB, MT, log=True)
    out = [_open(title, h), _title(title),
           _frame(xs, ys, [(v, fmt_num(v)) for v in xt], [(v, fmt_num(v)) for v in yt], xlabel, ylabel, h)]
    labels = []
    for g in groups:
        if g.curve:
            coords = " ".join(f"{xs(x):.1f},{ys(y):.1f}" for x, y in g.curve if x > 0 and y > 0)
            out.append(f'<polyline class="ln s{g.idx % 6}" fill="none" stroke-dasharray="6 4" points="{coords}"/>')
        for x, y, tip in g.points:
            out.append(f'<circle class="mk f{g.idx % 6}" cx="{xs(x):.1f}" cy="{ys(y):.1f}" r="5">'
                       f"<title>{escape(tip)}</title></circle>")
        anchor = g.curve[-1] if g.curve else (g.points[-1][0], g.points[-1][1])
        labels.append((ys(anchor[1]), g.name, g.idx))
    out.append(_direct_labels(labels))
    return "".join(out) + "</svg>"


def heatmap(
    title: str, rows: Sequence[str], cells: dict[tuple[int, int], tuple[float, str]],
    vmax: float, idx: int = 0,
) -> str:
    """cells maps (row, hour) to (loss %, tooltip). Missing cells stay empty."""
    if not rows or not cells:
        return _empty(title, "No data in this range.")
    cw = (W - ML - MR) / 24
    ch = min(22.0, max(10.0, 260.0 / len(rows)))
    h = int(MT + len(rows) * ch + MB)
    out = [_open(title, h), _title(title)]
    for r, label in enumerate(rows):
        y = MT + r * ch
        out.append(f'<text class="tk" x="{ML - 6}" y="{y + ch * 0.7:.1f}" text-anchor="end">{escape(label)}</text>')
        for c in range(24):
            x = ML + c * cw
            if (r, c) not in cells:
                out.append(f'<rect class="cell0" x="{x:.1f}" y="{y:.1f}" width="{cw - 1:.1f}" height="{ch - 1:.1f}"/>')
                continue
            v, tip = cells[(r, c)]
            op = 0.10 + 0.90 * min(v / vmax, 1.0) if vmax > 0 else 0.10
            out.append(f'<rect class="s{idx % 6} cellv" fill-opacity="{op:.2f}" x="{x:.1f}" y="{y:.1f}" '
                       f'width="{cw - 1:.1f}" height="{ch - 1:.1f}"><title>{escape(tip)}</title></rect>')
    for c in range(0, 24, 3):
        out.append(f'<text class="tk" x="{ML + c * cw + cw / 2:.1f}" y="{h - MB + 15}" text-anchor="middle">{c:02d}</text>')
    out.append(f'<text class="lb" x="{(ML + W - MR) / 2:.0f}" y="{h - 5}" text-anchor="middle">hour of day (local time)</text>')
    out.append(f'<text class="dl f{idx % 6}" x="{W - MR + 6}" y="{MT + 12}">stronger color</text>')
    out.append(f'<text class="dl f{idx % 6}" x="{W - MR + 6}" y="{MT + 26}">= more loss</text>')
    out.append(f'<text class="tk" x="{W - MR + 6}" y="{MT + 42}">full = {fmt_num(vmax)} %</text>')
    return "".join(out) + "</svg>"
