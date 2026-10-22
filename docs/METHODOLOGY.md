# Methodology

How each number is computed, and where it can mislead. Read this before putting a number in a report.

## Probes

| Probe | What is measured | Loss means |
|---|---|---|
| `ping` | ICMP echo through the system `ping` | No reply before ping gave up on that sequence number |
| `udp` | UDP echo to `lacuna server` | No echo within the grace time (default 1.5 s) plus the stats exchange |
| `tcp` | Time for `connect()` to complete | Refused, unreachable or timed out (2 s) connect |
| `throughput` | Bytes through TCP streams for a fixed time | Not loss. Retransmits come from `TCP_INFO` |

These three kinds of loss are different things. Routers often rate-limit or deprioritise ICMP, so ICMP loss can be higher or lower than loss seen by real traffic. A failed TCP connect is a connection-level event, not a lost packet. The report uses UDP and ICMP rows for packet-loss statistics and falls back to all rows only when a profile has neither.

## Loss

```
loss % = (sent - received) / sent
```

A packet that arrives after the grace time, but before the stats exchange finishes, still counts as received. Anything later counts as lost.

Uplink and downlink loss come from the server's bitmap of received sequence numbers:

```
uplink loss   = (sent - server_rx) / sent
downlink loss = (server_rx - echoed) / server_rx
```

The STATS request is retried up to 5 times. If every attempt is lost, the direction fields are stored as NULL and only total loss is reported.

Sample size matters more than anything else. Rough 95 % interval for a measured loss of `p` over `n` packets is `p +- 1.96 * sqrt(p(1-p)/n)`. With 500 packets at 1 % loss that is about 0.1 % to 1.9 %. The normal approximation is poor with so few events, so treat it as a rough guide.

## RTT

UDP RTT is `monotonic_now - t_send_ns`, both from the client's clock, so no clock sync is needed. It includes the server's receive and send scheduling. ICMP RTT is whatever `ping` prints, which is millisecond resolution with 1 or 3 decimals depending on the platform. TCP connect time is one RTT plus the remote kernel's SYN handling.

Percentiles use linear interpolation between order statistics. p99 of 100 samples is close to the maximum, so treat it as rough below a few hundred samples.

## Jitter

RFC 3550 interarrival jitter, section 6.4.1: for consecutive received packets, `D = |transit(i) - transit(i-1)|` and `J = J + (D - J) / 16`.

Lacuna has only the sender's clock, so it uses RTT as `transit`. The result is smoothed RTT variation, not one-way delay variation. If the forward and reverse paths vary in opposite directions, the RTT view can hide it.

## Loss bursts and the Gilbert-Elliott fit

A burst is a run of consecutive lost sequence numbers. The histogram counts runs by length. The dashed overlay shows what independent loss at the same overall rate would give: burst length is geometric, `P(L = k) = p^(k-1) (1 - p)`, scaled to the number of bursts seen.

The fit uses the simple Gilbert form of the model: loss probability 0 in the good state and 1 in the bad state.

```
p = P(good -> bad)   r = P(bad -> good)
mean burst = 1 / r   stationary loss = p / (p + r)
```

Both are maximum likelihood estimates from transition counts. For several runs the counts are pooled without joining the end of one run to the start of the next. Fits from fewer than 200 packets are flagged. With no losses `r` is undefined and is stored as NULL.

Known bias: if the path alternates between a clean and a congested regime, a two-state chain with one `p` and `r` will average them. Look at the timeline too.

## Random versus bursty verdict

The Wald-Wolfowitz runs test counts runs of equal symbols in the loss sequence and compares with what independent loss would give. Fewer runs than expected gives negative `z`, which means clumped loss. Per-run `z` values are combined with Stouffer's method, `Z = sum(z) / sqrt(k)`, over runs of 50 packets or more.

- `Z < -1.96`: "random model rejected" (one-sided, about 2.5 %)
- fewer than 10 lost packets in total: no verdict, too little data
- otherwise: "random model not rejected", which is weaker than "loss is random"

No correction is made for looking at many profiles.

## Throughput

`lacuna throughput HOST:PORT` opens N TCP streams to the server's TCP port, which is the UDP port plus one.

- Up: the client sends for `--secs`, half-closes, and the server replies with the byte count it received.
- Down: the server sends for `--secs` and closes. The client counts what it read.

```
Mbit/s = bytes * 8 / elapsed / 1e6
```

`elapsed` runs from before the connects to after the last stream finishes, so it includes handshakes and the drain. Short tests include slow start and read low. Parallel streams are summed. Per-stream rates are in the run's params.

`--url` uses `http.client`, N threads, re-requesting until time is up, and counts body bytes. It measures the path to that CDN edge, not to a server you control. TLS handshakes are inside the timing.

### TCP_INFO

On Linux Lacuna reads `TCP_INFO` before and after each stream and stores:

| Column | Value |
|---|---|
| `retrans` | Sum over streams of the change in `tcpi_total_retrans`. Only for `up`, because for `down` the retransmits happen on the sender (the server) and the client cannot see them |
| `cwnd` | Mean final `tcpi_snd_cwnd` in segments |
| `rtt_ms` | Mean final `tcpi_rtt` |
| `ssthresh` | Mean final `tcpi_snd_ssthresh` |

The struct layout was checked against `linux/tcp.h` and is unit tested with a synthetic buffer. It is not exercised against a real kernel on every platform. Off Linux the columns are NULL.

## Mathis model

```
rate <= (MSS / RTT) * C / sqrt(p),   C = sqrt(3/2) ~ 1.22
```

Reference: Mathis, Semke, Mahdavi and Ott, "The macroscopic behavior of the TCP congestion avoidance algorithm", ACM CCR 27(3), 1997.

Lacuna pairs each throughput sample with the nearest UDP or ICMP loss sample from the same profile within 5 minutes, takes `p` and the median RTT from that sample, uses an MSS of 1448 bytes, and skips pairs where `p` is 0 (the model is unbounded there).

Limits:

- The model is about the loss event rate for a long-lived Reno-like flow. Probe loss is packet loss on a different flow at a different time.
- CUBIC and BBR do not follow it closely. The ratio measured / predicted is a comparison, not a pass or fail.
- At low loss the model predicts more than the link can carry. Expect the measured value to sit below it.
- Bursty loss produces fewer loss events than lost packets, so the model underestimates throughput on bursty paths.

## Time axes and hour of day

Timestamps are stored as UTC unix time. The report converts to the local time of the machine that builds it. The hour-by-day heatmap weights each cell by packets sent. Cells with no data are drawn empty.

## Probe load

At defaults the probes add little traffic: ICMP about 5 pps, UDP 20 pps with 200 byte packets (about 32 kbit/s), TCP connects 2 per second. A throughput test saturates the path for its duration, so it can raise RTT and loss for other traffic and for your own probes. Do not interpret loss that appears during a throughput test as the path's idle loss.

## Not covered

IPv6, one-way delay, per-hop loss, path MTU, and probes from several vantage points at once.
