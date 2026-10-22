"""Build the single-file HTML report from the database."""

from __future__ import annotations

import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from html import escape

from lacuna import __version__
from lacuna.analysis import gilbert, mathis, stats
from lacuna.db import Database
from lacuna.report import svg

DOC_URL = "https://github.com/tejgokani/lacuna-probe/blob/main/docs/METHODOLOGY.md"
PACKET_KINDS = ("udp", "icmp")
MIN_SEQ_LEN = 50
MIN_LOSSES_FOR_VERDICT = 10

CSS = """
:root{--bg:#fbfbfa;--fg:#1b1f23;--mut:#5b6670;--card:#fff;--line:#d9dde1;--warn:#fff4d6;--warnfg:#5c4300;
--c0:#0072B2;--c1:#D55E00;--c2:#009E73;--c3:#CC79A7;--c4:#E69F00;--c5:#56B4E9}
@media (prefers-color-scheme:dark){:root{--bg:#111417;--fg:#e8eaec;--mut:#9aa5ae;--card:#1a1e22;--line:#2f353b;
--warn:#3a2f10;--warnfg:#f3d98b;--c0:#5AB4F0;--c1:#FF8A4C;--c2:#2FD3A0;--c3:#E59AC4;--c4:#F2B83A;--c5:#7CC5F2}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:1.6rem;margin:0 0 4px}
h2{font-size:1.15rem;margin:34px 0 8px;padding-top:6px;border-top:1px solid var(--line)}
p,li{max-width:70ch}
.meta{color:var(--mut);font-size:.9rem}
.banner{background:var(--warn);color:var(--warnfg);padding:10px 14px;border-radius:8px;margin:14px 0}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:10px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.card b{display:block;font-size:1.35rem;font-variant-numeric:tabular-nums}
.card span{color:var(--mut);font-size:.8rem}
.verdict{font-weight:600;margin:6px 0 16px}
.prof{display:inline-block;width:.8em;height:.8em;border-radius:2px;margin-right:6px;vertical-align:-1px}
.chart{width:100%;height:auto;display:block;margin:8px 0;background:var(--card);border:1px solid var(--line);border-radius:10px}
.chart .ct{font-size:13px;font-weight:600;fill:var(--fg)}
.chart .tk{font-size:10.5px;fill:var(--mut)}
.chart .lb{font-size:11px;fill:var(--mut)}
.chart .dl{font-size:11px;font-weight:600}
.chart .gr{stroke:var(--line);stroke-width:1}
.chart .ax{stroke:var(--mut);stroke-width:1}
.chart .ln{stroke-width:2;stroke-linejoin:round}
.chart .bar{opacity:.9}
.chart .cell0{fill:none;stroke:var(--line);stroke-width:.5}
.chart .cellv{stroke:none}
.chart .exp{stroke:var(--fg);fill:var(--fg)}
.chart .ln.exp{fill:none}
.chart .expt{fill:var(--fg);font-size:11px}
.s0{stroke:var(--c0);fill:var(--c0)}.s1{stroke:var(--c1);fill:var(--c1)}.s2{stroke:var(--c2);fill:var(--c2)}
.s3{stroke:var(--c3);fill:var(--c3)}.s4{stroke:var(--c4);fill:var(--c4)}.s5{stroke:var(--c5);fill:var(--c5)}
.f0{fill:var(--c0)}.f1{fill:var(--c1)}.f2{fill:var(--c2)}.f3{fill:var(--c3)}.f4{fill:var(--c4)}.f5{fill:var(--c5)}
.chart .ln{fill:none}
.chart .mk{stroke:var(--card);stroke-width:1}
.tablewrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.9rem;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:5px 9px;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{color:var(--mut);font-weight:600}
.note{color:var(--mut);font-size:.88rem}
.foot{margin-top:40px;color:var(--mut);font-size:.85rem}
a{color:var(--c0)}
"""


@dataclass(slots=True)
class Summary:
    profile: str
    loss_samples: int
    tput_samples: int
    sent: int
    lost: int
    loss_pct: float | None
    rtt_p95: float | None
    jitter_ms: float | None
    tput_median: float | None
    longest_burst: int | None
    ge: gilbert.GEFit | None
    verdict: str


# ---- aggregation ----

def _packet_rows(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    pk = [r for r in rows if r["kind"] in PACKET_KINDS]
    return pk or list(rows)


def _mean(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


def sequences(rows: list[sqlite3.Row]) -> list[list[int]]:
    out = []
    for r in rows:
        if r["loss_bits_hex"] and r["sent"] >= MIN_SEQ_LEN and r["kind"] in PACKET_KINDS:
            out.append(stats.hex_to_bits(r["loss_bits_hex"], r["sent"]))
    return out


def verdict_for(seqs: list[list[int]]) -> str:
    total = sum(len(s) for s in seqs)
    lost = sum(sum(s) for s in seqs)
    if not seqs:
        return "No stored loss sequences yet, so burst analysis is not possible."
    if lost == 0:
        return f"No loss in {total} packets."
    if lost < MIN_LOSSES_FOR_VERDICT:
        return f"Only {lost} of {total} packets lost. Too few to classify the pattern."
    runs = [n for s in seqs for n in stats.burst_lengths(s)]
    mean_burst = sum(runs) / len(runs)
    zs = [z for z in (stats.runs_test_z(s) for s in seqs) if z is not None]
    if not zs:
        return f"Mean burst {mean_burst:.1f} packets. Runs test not defined for this data."
    z = sum(zs) / math.sqrt(len(zs))
    if z < -1.96:
        return f"Bursty loss: mean burst {mean_burst:.1f} packets, random model rejected (z = {z:.1f})."
    return f"Loss looks random: mean burst {mean_burst:.1f} packets, random model not rejected (z = {z:.1f})."


def profile_summary(profile: str, loss_rows: list[sqlite3.Row], tput_rows: list[sqlite3.Row]) -> Summary:
    rows = _packet_rows(loss_rows)
    sent = sum(r["sent"] for r in rows)
    lost = sum(r["sent"] - r["recv"] for r in rows)
    seqs = sequences(rows)
    bursts = [r["max_burst"] for r in rows if r["max_burst"] is not None]
    return Summary(
        profile=profile,
        loss_samples=len(rows),
        tput_samples=len(tput_rows),
        sent=sent,
        lost=lost,
        loss_pct=100.0 * lost / sent if sent else None,
        rtt_p95=_mean([r["rtt_p95"] for r in rows if r["rtt_p95"] is not None]),
        jitter_ms=_mean([r["jitter_ms"] for r in rows if r["jitter_ms"] is not None]),
        tput_median=stats.median([r["mbps"] for r in tput_rows]),
        longest_burst=max(bursts) if bursts else None,
        ge=gilbert.fit_many(seqs) if seqs else None,
        verdict=verdict_for(seqs),
    )


def _f(v: float | None, spec: str = ".2f", unit: str = "") -> str:
    return "n/a" if v is None else f"{v:{spec}}{unit}"


def _is_simulated(rows: list[sqlite3.Row]) -> bool:
    return any('"simulated": true' in r["params_json"] for r in rows)


# ---- sections ----

def _cards(s: Summary) -> str:
    items = [
        (_f(s.loss_pct, ".2f", " %"), f"mean loss ({s.lost} of {s.sent} packets)"),
        (_f(s.rtt_p95, ".1f", " ms"), "p95 RTT (mean of runs)"),
        (_f(s.jitter_ms, ".2f", " ms"), "mean jitter (RFC 3550)"),
        (_f(s.tput_median, ".1f", " Mbit/s"), f"median throughput ({s.tput_samples} test{'' if s.tput_samples == 1 else 's'})"),
        (str(s.longest_burst) if s.longest_burst is not None else "n/a", "longest burst (packets)"),
    ]
    cards = "".join(f'<div class="card"><b>{escape(v)}</b><span>{escape(k)}</span></div>' for v, k in items)
    return f'<div class="cards">{cards}</div><p class="verdict">{escape(s.verdict)}</p>'


def _swatch(i: int) -> str:
    return f'<span class="prof" style="background:var(--c{i % 6})"></span>'


def _bucket(pts: list[tuple[float, float]], n: int = 240) -> list[tuple[float, float]]:
    if len(pts) <= n:
        return pts
    t0, t1 = pts[0][0], pts[-1][0]
    width = (t1 - t0) / n or 1.0
    acc: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for t, v in pts:
        acc[min(int((t - t0) / width), n - 1)].append((t, v))
    return [(sum(t for t, _ in g) / len(g), sum(v for _, v in g) / len(g)) for _, g in sorted(acc.items())]


def _tips(pts: list[tuple[float, float]], unit: str) -> list[str]:
    return [f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t))}: {svg.fmt_num(v)} {unit}" for t, v in pts]


def _series(name: str, idx: int, pts: list[tuple[float, float]], unit: str, dash: bool = False) -> svg.Series:
    pts = _bucket(pts)
    return svg.Series(name, idx, pts, _tips(pts, unit), dash)


def _timeline(profiles: list[str], loss: dict, tput: dict) -> str:
    times = [r["ts"] for p in profiles for r in loss[p]] + [r["ts"] for p in profiles for r in tput[p]]
    if not times:
        return "<p>No samples in this range.</p>"
    dom = (min(times), max(times))
    s_loss, s_rtt, s_tp = [], [], []
    multi = len(profiles) > 1
    for i, p in enumerate(profiles):
        rows = _packet_rows(loss[p])
        s_loss.append(_series(p, i, [(r["ts"], r["loss_pct"]) for r in rows], "% loss"))
        for key, tag, dash in (("rtt_p50", "p50", False), ("rtt_p95", "p95", True)):
            name = f"{p} {tag}" if multi else tag
            s_rtt.append(_series(name, i, [(r["ts"], r[key]) for r in rows if r[key] is not None], "ms", dash))
        s_tp.append(_series(p, i, [(r["ts"], r["mbps"]) for r in tput[p]], "Mbit/s"))
    return (
        svg.line_chart("Packet loss over time", s_loss, "time (local)", "loss (%)", dom)
        + svg.line_chart("Round-trip time over time (dashed = p95)", s_rtt, "time (local)", "RTT (ms)", dom)
        + svg.line_chart("Throughput over time", s_tp, "time (local)", "throughput (Mbit/s)", dom)
    )


def _bursts(profiles: list[str], loss: dict) -> str:
    out = []
    for i, p in enumerate(profiles):
        seqs = sequences(_packet_rows(loss[p]))
        runs = [n for s in seqs for n in stats.burst_lengths(s)]
        total = sum(len(s) for s in seqs)
        if not runs:
            out.append(svg.hist_chart(f"{p}: loss bursts", {}, [], i))
            continue
        pi = sum(runs) / total
        expected = stats.random_burst_expectation(pi, len(runs), max(runs))
        out.append(svg.hist_chart(f"{p}: loss burst lengths", stats.burst_histogram(runs), expected, i))
        fit = gilbert.fit_many(seqs)
        out.append(f'<p class="note">{_swatch(i)}{escape(p)}: {_ge_text(fit)}</p>')
    return "".join(out)


def _ge_text(fit: gilbert.GEFit) -> str:
    if fit.p is None or fit.r is None:
        return f"Gilbert-Elliott fit not available ({fit.note})."
    text = (f"Gilbert-Elliott fit p = {fit.p:.4f}, r = {fit.r:.3f}, mean burst {1 / fit.r:.1f} packets, "
            f"stationary loss {100 * (fit.stationary_loss or 0):.2f} %")
    return text + (f". Note: {fit.note}." if fit.note else ".")


def _heatmaps(profiles: list[str], loss: dict) -> str:
    out = []
    for i, p in enumerate(profiles):
        acc: dict[tuple[str, int], list[int]] = defaultdict(lambda: [0, 0])
        for r in _packet_rows(loss[p]):
            lt = time.localtime(r["ts"])
            cell = acc[(time.strftime("%m-%d", lt), lt.tm_hour)]
            cell[0] += r["sent"] - r["recv"]
            cell[1] += r["sent"]
        days = sorted({d for d, _ in acc})[-31:]
        cells = {}
        for (d, h), (lost, sent) in acc.items():
            if d in days and sent:
                pct = 100.0 * lost / sent
                cells[(days.index(d), h)] = (pct, f"{d} {h:02d}:00 loss {pct:.2f} % ({lost}/{sent} packets)")
        vmax = max((v for v, _ in cells.values()), default=0.0) or 1.0
        out.append(svg.heatmap(f"{p}: loss by hour of day", days, cells, vmax, i))
        if len(cells) < 6:
            out.append('<p class="note">Fewer than 6 hour cells have data. Run <code>lacuna monitor</code> '
                       "for a day or more to see a daily pattern.</p>")
    return "".join(out)


def _mathis(profiles: list[str], loss: dict, tput: dict, simulated: bool) -> str:
    all_loss = [r for p in profiles for r in loss[p]]
    pairs = mathis.pair_samples([r for p in profiles for r in tput[p]], all_loss)
    if not pairs:
        return ('<p>No throughput test has a UDP or ICMP loss sample from the same profile within '
                "5 minutes with loss above 0, so there is nothing to compare. Run <code>lacuna udp</code> "
                "or <code>lacuna ping</code> next to <code>lacuna throughput</code>.</p>")
    groups, rtts = [], []
    for i, p in enumerate(profiles):
        mine = [x for x in pairs if x.profile == p]
        if not mine:
            continue
        rtt = stats.median([x.rtt_ms for x in mine]) or 1.0
        lo, hi = min(x.loss_frac for x in mine), max(x.loss_frac for x in mine)
        grid = [lo / 3 * (hi * 3 / (lo / 3)) ** (k / 24) for k in range(25)]
        pts = [(x.loss_frac * 100, x.measured_mbps,
                f"{p}: loss {x.loss_frac * 100:.2f} %, measured {x.measured_mbps:.2f} Mbit/s, "
                f"Mathis {x.predicted_mbps:.2f} Mbit/s") for x in mine]
        curve = [(f * 100, v) for f, v in mathis.curve(mathis.DEFAULT_MSS, rtt, grid)]
        groups.append(svg.ScatterGroup(p, i, pts, curve))
        rtts.append(f"{p} {rtt:.0f} ms")
    chart = svg.scatter_chart("Measured throughput vs Mathis model (dashed)", groups,
                              "packet loss (%, log scale)", "throughput (Mbit/s, log scale)")
    note = (f'<p class="note">Dashed curves use MSS {mathis.DEFAULT_MSS} B and the median RTT of each profile ('
            f"{escape(', '.join(rtts))}).</p>")
    if simulated:
        note += ('<p class="note">The simulated TCP path in the demo has delay and a rate cap but no packet '
                 "loss, so measured throughput is not expected to follow the model.</p>")
    rows = "".join(
        f"<tr><td>{escape(x.profile)}</td><td>{x.loss_frac * 100:.2f}</td><td>{x.rtt_ms:.1f}</td>"
        f"<td>{x.measured_mbps:.2f}</td><td>{x.predicted_mbps:.2f}</td><td>{x.ratio:.2f}</td></tr>"
        for x in pairs[:12])
    table = ('<div class="tablewrap"><table><tr><th>profile</th><th>loss %</th><th>RTT ms</th>'
             "<th>measured Mbit/s</th><th>Mathis Mbit/s</th><th>ratio</th></tr>" + rows + "</table></div>")
    return chart + note + table


def _updown(profiles: list[str], loss: dict) -> str:
    ups, downs, cats = [], [], []
    for p in profiles:
        rows = [r for r in loss[p] if r["kind"] == "udp" and r["up_loss_pct"] is not None]
        if not rows:
            continue
        sent = sum(r["sent"] for r in rows)
        up_lost = sum(r["up_loss_pct"] / 100 * r["sent"] for r in rows)
        reached = sum(r["sent"] * (1 - r["up_loss_pct"] / 100) for r in rows)
        down_lost = sum((r["down_loss_pct"] or 0) / 100 * r["sent"] * (1 - r["up_loss_pct"] / 100) for r in rows)
        cats.append(p)
        ups.append(100 * up_lost / sent)
        downs.append(100 * down_lost / reached if reached else None)
    if not cats:
        return ("<p>No UDP runs with server statistics. Start <code>lacuna server</code> on another "
                "machine and run <code>lacuna udp HOST:9400</code> to split loss by direction.</p>")
    chart = svg.bar_chart("Uplink vs downlink loss", cats, [profiles.index(c) for c in cats],
                          [("up", 0.95, ups), ("down", 0.5, downs)], "loss (%)", lambda v: f"{v:.2f}")
    return chart + ('<p class="note">Up = client to server. Down = server to client, measured only on '
                    "packets the server received. Bar color is the profile, faded bars are downlink.</p>")


def _compare(profiles: list[str], summaries: list[Summary]) -> str:
    rows = "".join(
        f"<tr><td>{_swatch(i)}{escape(s.profile)}</td><td>{s.loss_samples}</td><td>{_f(s.loss_pct, '.2f')}</td>"
        f"<td>{_f(s.rtt_p95, '.1f')}</td><td>{_f(s.jitter_ms, '.2f')}</td><td>{_f(s.tput_median, '.1f')}</td>"
        f"<td>{s.longest_burst if s.longest_burst is not None else 'n/a'}</td>"
        f"<td>{_f(1 / s.ge.r if s.ge and s.ge.r else None, '.1f')}</td></tr>"
        for i, s in enumerate(summaries))
    return ('<div class="tablewrap"><table><tr><th>profile</th><th>runs</th><th>loss %</th><th>p95 RTT ms</th>'
            "<th>jitter ms</th><th>median Mbit/s</th><th>longest burst</th><th>GE mean burst</th></tr>"
            + rows + "</table></div>")


def _range_text(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "no samples"
    fmt = "%Y-%m-%d %H:%M"
    return f"{time.strftime(fmt, time.localtime(min(r['ts'] for r in rows)))} to " \
           f"{time.strftime(fmt, time.localtime(max(r['ts'] for r in rows)))} (local time)"


def build_report(
    db: Database, profiles: list[str] | None = None, since: float | None = None,
) -> str:
    profs = profiles or db.profiles()
    all_loss = db.loss_rows(profs or None, since)
    all_tput = db.tput_rows(profs or None, since)
    profs = [p for p in profs if any(r["profile"] == p for r in all_loss + all_tput)] or profs
    loss = {p: [r for r in all_loss if r["profile"] == p] for p in profs}
    tput = {p: [r for r in all_tput if r["profile"] == p] for p in profs}
    summaries = [profile_summary(p, loss[p], tput[p]) for p in profs]
    simulated = _is_simulated(all_loss + all_tput)
    return _page(profs, summaries, loss, tput, all_loss + all_tput, simulated)


def _page(profs, summaries, loss, tput, all_rows, simulated) -> str:
    head = ", ".join(f"{_swatch(i)}{escape(p)}" for i, p in enumerate(profs)) or "none"
    banner = ('<div class="banner"><b>Simulated data.</b> This report comes from <code>lacuna demo</code>: '
              "a local server behind a loss simulator. Nothing here describes a real network.</div>"
              ) if simulated else ""
    cards = "".join(
        (f"<h3>{_swatch(i)}{escape(s.profile)}</h3>" if len(profs) > 1 else "") + _cards(s)
        for i, s in enumerate(summaries)) or "<p>The database has no samples yet.</p>"
    compare = (f'<h2 id="compare">Profile comparison</h2>{_compare(profs, summaries)}'
               if len(profs) > 1 else "")
    n_loss, n_tput = sum(len(v) for v in loss.values()), sum(len(v) for v in tput.values())
    body = f"""
<h1>Lacuna report</h1>
<p class="meta">Profiles: {head}<br>Range: {escape(_range_text(all_rows))}<br>
Samples: {n_loss} loss, {n_tput} throughput. Lacuna {__version__}.</p>
{banner}
<h2 id="summary">Summary</h2>{cards}
<h2 id="timeline">Timeline</h2>{_timeline(profs, loss, tput)}
<h2 id="bursts">Loss bursts</h2>
<p class="note">Bars show how many bursts of each length were seen. The dashed line shows what independent
(random) loss at the same rate would give.</p>{_bursts(profs, loss)}
<h2 id="heatmap">Loss by hour of day</h2>{_heatmaps(profs, loss)}
<h2 id="mathis">Loss versus throughput</h2>{_mathis(profs, loss, tput, simulated)}
<h2 id="updown">Uplink versus downlink</h2>{_updown(profs, loss)}
{compare}
<h2 id="method">Methodology</h2>
<p class="foot">How each number is computed, and where it can mislead: <a href="{DOC_URL}">{DOC_URL}</a></p>
"""
    return (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="color-scheme" content="light dark">'
            f"<title>Lacuna report</title><style>{CSS}</style></head>\n<body><main>{body}</main></body></html>\n")
