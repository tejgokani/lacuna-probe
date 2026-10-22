"""ICMP through the system `ping` binary. No raw sockets, no root."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

from lacuna.analysis import stats
from lacuna.db import LossSample

_REPLY = re.compile(r"icmp_seq[=\s](\d+).*?time[=<]([\d.]+)\s*ms", re.I)
_SENT_RECV = re.compile(r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets\s+)?received", re.I)

# iputils (Linux) counts icmp_seq from 1, BSD and macOS ping from 0.
SEQ_BASE = 0 if sys.platform == "darwin" else 1


class PingError(RuntimeError):
    pass


@dataclass(slots=True)
class PingResult:
    transmitted: int
    received: int
    replies: list[tuple[int, float]] = field(default_factory=list)  # (icmp_seq, rtt_ms)
    duplicates: int = 0


def parse_ping(text: str, seq_base: int = SEQ_BASE) -> PingResult:
    """Parse Linux (iputils) or macOS ping output."""
    replies: list[tuple[int, float]] = []
    dups = 0
    seen: set[int] = set()
    for line in text.splitlines():
        m = _REPLY.search(line)
        if not m or "bytes from" not in line:
            continue
        seq, rtt = int(m.group(1)), float(m.group(2))
        if seq in seen or "DUP" in line:
            dups += 1
            continue
        seen.add(seq)
        replies.append((seq - seq_base, rtt))
    m = _SENT_RECV.search(text)
    if not m:
        raise PingError("no summary line in ping output")
    return PingResult(int(m.group(1)), int(m.group(2)), replies, dups)


def sample_from_result(res: PingResult) -> LossSample:
    sent = res.transmitted
    got = {seq for seq, _ in res.replies if 0 <= seq < sent}
    bits = [0 if i in got else 1 for i in range(sent)]
    ordered = [rtt for _, rtt in sorted(res.replies)]
    rtts = stats.rtt_summary(ordered)
    recv = sent - sum(bits)
    bursts = stats.burst_lengths(bits)
    return LossSample(
        sent=sent,
        recv=recv,
        loss_pct=stats.loss_pct(sent, recv),
        jitter_ms=stats.rfc3550_jitter(ordered) if len(ordered) > 1 else None,
        dup=res.duplicates,
        max_burst=max(bursts) if bursts else 0,
        loss_bits_hex=stats.bits_to_hex(bits),
        **rtts,
    )


def run_ping(target: str, count: int = 100, interval: float = 0.2) -> LossSample:
    exe = shutil.which("ping")
    if not exe:
        raise PingError("ping binary not found. On Ubuntu: sudo apt install iputils-ping")
    cmd = [exe, "-n", "-c", str(count), "-i", f"{interval:g}", target]
    env = dict(os.environ, LC_ALL="C")
    deadline = count * interval + count * 0.05 + 15
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=deadline, env=env)
    except subprocess.TimeoutExpired:
        raise PingError(f"ping {target} timed out") from None
    out = proc.stdout
    if proc.returncode not in (0, 1) or "packets transmitted" not in out:
        msg = (proc.stderr or out).strip().splitlines()
        raise PingError(msg[-1] if msg else f"ping exited with {proc.returncode}")
    return sample_from_result(parse_ping(out))
