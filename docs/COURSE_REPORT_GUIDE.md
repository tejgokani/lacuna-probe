# Turning Lacuna output into a Computer Networks course report

Topic: Packet Loss and Throughput Analysis in Networks. This is a layout, not a template to fill blindly. Use your own numbers and say what you saw.

## What you need before you start

| Item | Why |
|---|---|
| Two or more network conditions | A comparison is the interesting result. For example campus WiFi, hotspot, wired |
| One machine you control, running `lacuna server` | Only way to split uplink and downlink loss |
| At least a few hours of `lacuna monitor` per profile | Short runs cannot show a daily pattern or enough loss events |
| The `lacuna-report.html` per condition | Source of every figure below |

## Suggested structure

| Section | Content | Where the material comes from |
|---|---|---|
| Abstract | Question, method, two or three numbers, one sentence of conclusion | Summary cards |
| Introduction | Why loss and throughput are linked. Define loss, jitter, RTT, goodput | Course notes, `docs/METHODOLOGY.md` |
| Method | Probes used, rates, durations, which network each profile was, how the far end was set up | Your commands, the architecture diagram in `docs/ARCHITECTURE.md` |
| Results | One subsection per profile, then the comparison | Report sections 2 to 8 |
| Analysis | Is loss bursty, does Mathis fit, which direction loses | Burst histogram, Mathis scatter, up/down bars |
| Limitations | Be specific, see list below | `docs/METHODOLOGY.md` |
| Conclusion | Answer the question you opened with | |
| References | See below | |

## Commands to cite in the Method section

```bash
lacuna server                                              # far end
lacuna monitor --profile campus-wifi                       # on the campus network
lacuna monitor --profile hotspot                           # on the hotspot
lacuna compare campus-wifi hotspot --since 1d
lacuna report --profile campus-wifi --profile hotspot --since 1d -o report.html
lacuna export --format csv --since 1d -o data.csv          # for your own plots or a table appendix
```

State the rates. Defaults are in `README.md`. Report the packet count behind every loss figure.

## Reading each report section

| Report section | What to say about it |
|---|---|
| Summary cards | Quote mean loss with the packet count, p95 RTT, jitter. Copy the verdict line exactly |
| Timeline | Point at specific events: a loss spike, an RTT step. Note the time and what you were doing |
| Loss bursts | If bars sit above the dashed line at lengths 2 and up, loss is clumped. Quote mean burst from the Gilbert-Elliott line |
| Hour by day | Needs a day or more. Name the worst hours. If you have one day, say it is one day |
| Loss vs throughput | Quote the measured / predicted ratio for each pair. Discuss why it is not 1 (see Mathis limits in `docs/METHODOLOGY.md`) |
| Uplink vs downlink | Say which is worse and give the two percentages |

## Questions a marker is likely to ask

- How many packets support this loss figure? (Report it. 5 events is not a measurement of 1 %.)
- Is the loss random? (Runs test and burst histogram, with the sample size.)
- Why does measured throughput differ from Mathis? (Bursty loss, CUBIC or BBR instead of Reno, short test with slow start, loss measured on another flow, a rate cap in the path.)
- Does ICMP loss equal TCP loss? (No. Routers deprioritise ICMP.)
- What did your own probing cost? (About 32 kbit/s for UDP at defaults. Throughput tests saturate the link while they run.)

## Limitations worth stating

- Single vantage point, one far-end server, IPv4 only.
- RTT stands in for transit time in the jitter figure.
- Loss was measured with UDP probes, which may be treated differently from your application's traffic.
- Throughput tests are short and include slow start.
- If you used `lacuna demo` for any figure, say it is simulated.

## References

- M. Mathis, J. Semke, J. Mahdavi, T. Ott. The macroscopic behavior of the TCP congestion avoidance algorithm. ACM SIGCOMM Computer Communication Review 27(3), 1997.
- E. N. Gilbert. Capacity of a burst-noise channel. Bell System Technical Journal 39(5), 1960.
- E. O. Elliott. Estimates of error rates for codes on burst-noise channels. Bell System Technical Journal 42(5), 1963.
- H. Schulzrinne, S. Casner, R. Frederick, V. Jacobson. RTP: A Transport Protocol for Real-Time Applications. RFC 3550, 2003 (interarrival jitter, section 6.4.1).

Check each reference against the original before you cite it.
