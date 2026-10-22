"""`lacuna demo`: offline end to end run against a local lossy path.

    client ---> [ lossim :base+10 ] ---> [ lacuna server :base ]
                 drop p%, add delay

The data is simulated and is marked as such in the stored params.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from pathlib import Path

from lacuna import server as S
from lacuna.config import Job, data_dir
from lacuna.db import Database
from lacuna.lossim import (
    GilbertLoss,
    LossModel,
    NoLoss,
    TcpDelayProxy,
    UdpLossSim,
    UniformLoss,
    split_loss,
)
from lacuna.monitor import Outcome, store
from lacuna.probes.throughput import run_tcp_throughput
from lacuna.probes.udp import run_udp
from lacuna.report.html import build_report

SEED = 20260101
LEVELS = (0.0, 0.5, 1.0, 2.0, 5.0, 10.0)  # round-trip loss, percent
MEAN_BURST = 4.0
COUNT, PPS, SIZE = 400, 180.0, 200
DELAY_MS = 10.0  # each way
RATE_MBPS = 50.0  # TCP path shaping
PROFILES = ("demo-uniform", "demo-bursty")


def demo_db_path() -> Path:
    return data_dir() / "demo.db"


def _models(kind: str, loss_pct: float, seed: int) -> tuple[LossModel, LossModel]:
    if loss_pct <= 0:
        return NoLoss(), NoLoss()
    up, down = split_loss(loss_pct / 100.0)
    if kind == "uniform":
        return UniformLoss(up, seed), UniformLoss(down, seed + 1)
    return GilbertLoss(up, MEAN_BURST, seed), GilbertLoss(down, MEAN_BURST, seed + 1)


def _udp_step(db, sim, port, kind, loss_pct, i, log) -> None:
    seed = SEED + i * 10 + (0 if kind == "uniform" else 5)
    sim.up, sim.down = _models(kind, loss_pct, seed)
    sample = run_udp("127.0.0.1", port, COUNT, PPS, SIZE, grace=0.5, rng=random.Random(seed))
    params = {"count": COUNT, "pps": PPS, "size": SIZE, "simulated": True, "model": kind,
              "target_loss_pct": loss_pct, "delay_ms": DELAY_MS * 2}
    store(db, f"demo-{kind}", Job("udp", f"127.0.0.1:{port}", 0), Outcome(sample, params))
    log(f"  {kind:<8} set {loss_pct:4.1f}%  measured {sample.loss_pct:5.2f}%  "
        f"up {sample.up_loss_pct or 0:5.2f}%  down {sample.down_loss_pct or 0:5.2f}%  "
        f"longest burst {sample.max_burst}")


def _tput_step(db, port, profile, log) -> None:
    sample, per = run_tcp_throughput("127.0.0.1", port, secs=2, streams=2, direction="up")
    params = {"secs": 2, "streams": 2, "per_stream_mbps": per, "simulated": True,
              "delay_ms": DELAY_MS * 2, "rate_cap_mbps": RATE_MBPS}
    store(db, profile, Job("throughput", f"127.0.0.1:{port}", 0), Outcome(sample, params))
    log(f"  {profile:<12} TCP up through delayed path: {sample.mbps:.0f} Mbit/s")


def run_demo(
    out: Path, base_port: int = 9400, log: Callable[[str], None] = print,
    db_path: Path | None = None,
) -> Path:
    path = db_path or demo_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    sim_port = base_port + 10
    srv = S.LacunaServer("127.0.0.1", base_port)
    sim = UdpLossSim(("127.0.0.1", sim_port), ("127.0.0.1", base_port), delay_ms=DELAY_MS)
    proxy = TcpDelayProxy(("127.0.0.1", sim_port + 1), ("127.0.0.1", base_port + 1), DELAY_MS, RATE_MBPS)
    started: list = []
    try:
        for comp in (srv, sim, proxy):
            comp.start()
            started.append(comp)
        with Database(path) as db:
            log(f"demo: {COUNT}-packet UDP runs through a simulated lossy path (data is simulated)")
            for i, level in enumerate(LEVELS):
                for kind in ("uniform", "bursty"):
                    _udp_step(db, sim, sim_port, kind, level, i, log)
            for profile in PROFILES:
                _tput_step(db, sim_port, profile, log)
            out.write_text(build_report(db), encoding="utf-8")
    finally:
        for comp in reversed(started):
            comp.stop()
    return out
