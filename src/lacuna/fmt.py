"""Plain-text formatting of probe results, shared by the CLI and the TUI."""

from __future__ import annotations

from lacuna.config import Job
from lacuna.db import LossSample, TputSample


def kv_table(rows: list[tuple[str, str]]) -> str:
    w = max(len(k) for k, _ in rows)
    return "\n".join(f"{k:<{w}}  {v}" for k, v in rows)


def fnum(v: float | None, spec: str = ".2f") -> str:
    return "n/a" if v is None else format(v, spec)


def format_loss(job: Job, profile: str, s: LossSample) -> str:
    rows = [
        ("target", f"{job.target} ({job.kind})"),
        ("profile", profile),
        ("packets", f"{s.sent} sent, {s.recv} received"),
        ("loss", f"{s.loss_pct:.2f} %"),
    ]
    if s.up_loss_pct is not None:
        rows.append(("direction", f"uplink {s.up_loss_pct:.2f} %, downlink {fnum(s.down_loss_pct)} %"))
    rows.append(("rtt ms", "  ".join(f"{k} {fnum(getattr(s, 'rtt_' + k), '.2f')}"
                                     for k in ("min", "mean", "p50", "p95", "p99", "max"))))
    rows.append(("jitter", f"{fnum(s.jitter_ms)} ms"))
    extra = f"longest burst {s.max_burst}"
    if s.dup is not None:
        extra += f", duplicates {s.dup}"
    if s.reordered is not None:
        extra += f", reordered {s.reordered}"
    rows.append(("bursts", extra))
    if s.ge_p is not None and s.ge_r:
        rows.append(("gilbert", f"p {s.ge_p:.4f}  r {s.ge_r:.3f}  mean burst {1 / s.ge_r:.1f}"
                     + ("  (small sample, treat as rough)" if s.sent < 200 else "")))
    return kv_table(rows)


def format_tput(job: Job, profile: str, s: TputSample, per: list[float]) -> str:
    rows = [
        ("target", f"{job.target} ({job.kind})"),
        ("profile", profile),
        ("direction", f"{s.direction}, {s.streams} stream(s), {s.secs:.1f} s"),
        ("transferred", f"{s.bytes / 1e6:.2f} MB"),
        ("throughput", f"{s.mbps:.2f} Mbit/s total"),
    ]
    if len(per) > 1:
        rows.append(("per stream", "  ".join(f"{m:.2f}" for m in per) + " Mbit/s"))
    if s.cwnd is None:
        rows.append(("tcp_info", "not available on this OS"))
    else:
        retrans = "n/a (receiver side)" if s.retrans is None else str(s.retrans)
        rows.append(("tcp_info", f"retrans {retrans}  cwnd {s.cwnd}  rtt {fnum(s.rtt_ms, '.1f')} ms  "
                                 f"ssthresh {s.ssthresh}"))
    return kv_table(rows)


