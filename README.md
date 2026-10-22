# Lacuna

Measure real packet loss, jitter, RTT and throughput on a live network path, keep the history in SQLite, and get one self-contained HTML report.

[![CI](https://github.com/tejgokani/lacuna-probe/actions/workflows/ci.yml/badge.svg)](https://github.com/tejgokani/lacuna-probe/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)

A lacuna is a gap, here the missing packets. Lacuna answers questions like:

- How much loss does my campus WiFi have compared with my phone hotspot?
- Is the loss random or bursty?
- Does measured TCP throughput match the Mathis model for the loss and RTT I see?
- Is the loss on the uplink or the downlink?
- Which hours of the day are worst?

It runs real probes against real hosts. The only simulated data comes from `lacuna demo`, which exists so the tool and the tests work offline.

> **Screenshot:** run `lacuna demo`, or open [examples/sample-report/index.html](examples/sample-report/index.html) in a browser after cloning. The sample is simulated data and is labelled that way inside the report.

## Quickstart (30 seconds)

```bash
git clone https://github.com/tejgokani/lacuna-probe.git
cd lacuna-probe
./install.sh
lacuna demo
```

`lacuna demo` runs for about 40 seconds with no internet and writes `lacuna-report.html`.

Or just type `lacuna` with no arguments. That opens the full-screen app described below.

Requirements: Python 3.10 or newer. Runtime dependencies: none (Python 3.10 also needs `tomli`, which pip installs for you). No root. ICMP uses the system `ping` binary. Also works with `pip install .` and `pipx install .`.

## The app: just type `lacuna`

Run `lacuna` with no arguments in a terminal and a full-screen app opens. Everything below is also available as commands, but you never need to remember one.

| Screen | Keys | What it does |
|---|---|---|
| 1 Dashboard | `p` profile, `w` window, `d` demo data | Live loss, RTT, jitter, throughput, burst verdict and sparklines |
| 2 Probes | `Enter` edit, `r` run | Ping, UDP, TCP, throughput and HTTP tests from a form, plus the offline demo |
| 3 Monitor | `s` start or stop | Runs your probe jobs on a schedule in the background, with a live log |
| 4 Server | `s` start or stop | Be the far end for UDP and throughput tests, see live sessions |
| 5 Report | `r` HTML, `c` CSV, `j` JSON, `o` open | Pick profiles and a time window, write files |
| 6 Settings | `Enter` edit | Profile name, database, refresh rate, server port, folders, colors. Saved as you go |
| 7 Help | `?` from anywhere | Help center: probes explained, reading the numbers, safety, troubleshooting |

`1` to `7` jump between screens, `Tab` cycles, `q` quits and stops anything it started. The app enforces the same rate caps as the CLI. It needs a terminal of at least 60 by 14. It uses `curses` from the standard library, so there is nothing extra to install.

New here? Press `2`, move to the probe selector, choose "Offline demo", press `Enter`, then `1` and `d` once it finishes.

## How it fits together

```
                      +-----------------------------+
 lacuna ping   -----> | system ping (no raw socket) |--> 1.1.1.1, 8.8.8.8 ...
 lacuna tcp    -----> | TCP connect timer           |--> host:443
 lacuna udp    -----> | UDP probes, seq numbers     |<-> lacuna server (UDP :9400)
 lacuna throughput -> | TCP / HTTP transfer         |<-> lacuna server (TCP :9401)
 lacuna monitor ----> | scheduler runs all of these |
                      +--------------+--------------+
                                     |
                                     v
                      +-----------------------------+
                      | SQLite  ~/.local/share/     |
                      |         lacuna/lacuna.db    |
                      +------+----------+-----------+
                             |          |
                  lacuna report     lacuna tui / compare / export
                  (one HTML file)
```

## Commands

| Command | What it does |
|---|---|
| `lacuna` | Open the full-screen app (needs a terminal) |
| `lacuna demo` | Offline end to end run: local server behind a loss simulator, builds a report |
| `lacuna server` | UDP echo and TCP throughput server. Run it on the far end of the path |
| `lacuna ping TARGET` | ICMP loss and RTT through the system `ping` |
| `lacuna udp HOST:PORT` | UDP probes. Splits loss into uplink and downlink, fits a burst model |
| `lacuna tcp HOST:PORT` | TCP connect time and failures |
| `lacuna throughput HOST:PORT` | TCP up or down throughput to a Lacuna server, with `TCP_INFO` on Linux |
| `lacuna throughput --url URL` | HTTP download throughput |
| `lacuna monitor` | Run probes on a schedule until Ctrl+C |
| `lacuna tui` | Same as `lacuna`. Without a terminal it prints one dashboard snapshot |
| `lacuna report` | Build the HTML report |
| `lacuna compare A B` | Two profiles side by side in the terminal |
| `lacuna export` | CSV or JSON dump |
| `lacuna doctor` | Check Python, ping, database path, permissions |

Every probe command prints a table, stores the result, and takes `--profile NAME`, `--no-store`, `--json`. Full flag list: [docs/CLI.md](docs/CLI.md).

## Worked example: campus WiFi vs phone hotspot over a day

You need one machine you control as the far end, for example a small cloud VM with UDP 9400 and TCP 9401 open.

```bash
# 1. On the far end
lacuna server

# 2. On your laptop, on campus WiFi. Uncomment the udp and throughput jobs
#    in the config and point them at your server.
cp examples/lacuna.toml ~/.config/lacuna/lacuna.toml    # mkdir -p ~/.config/lacuna first
lacuna monitor --profile campus-wifi

# 3. Later in the day, switch the laptop to the phone hotspot and keep going
lacuna monitor --profile hotspot

# 4. Compare
lacuna compare campus-wifi hotspot --since 1d
lacuna report --profile campus-wifi --profile hotspot --since 1d -o wifi-vs-hotspot.html
```

For a quick one-off instead of a day-long run:

```bash
lacuna ping 1.1.1.1 --count 100 --profile campus-wifi
lacuna udp YOUR.SERVER:9400 --count 1000 --pps 50 --profile campus-wifi
lacuna throughput YOUR.SERVER:9400 --secs 10 --direction up --profile campus-wifi
```

Run `lacuna server` and `lacuna udp` on two machines to get uplink and downlink loss separately. ICMP and TCP connect cannot tell the directions apart.

## What the numbers mean

| Number | Meaning | Watch out for |
|---|---|---|
| Loss % | Probes sent that never came back | Few packets means a wide error bar. 500 packets at 1 % loss is about 5 events |
| Uplink / downlink loss | Probes the server never saw, and echoes the client never saw | Needs `lacuna server`. Direction fields are empty if the stats exchange itself was lost |
| Burst length | Consecutive lost packets. Mean burst is `1/r` in the Gilbert-Elliott fit | A mean burst near 1 means random loss. 3 or more means congestion or a flapping link |
| Random model rejected | Runs test says loss is clumpier than independent drops (z < -1.96) | Needs about 10 or more lost packets to say anything |
| RTT p50, p95, p99 | Round-trip time percentiles. p95 is what interactive traffic feels | UDP RTT includes the server's scheduling delay |
| Jitter | RFC 3550 smoothed change in RTT between consecutive replies | Here RTT stands in for one-way transit, so it is RTT variation |
| Throughput | Bytes moved divided by wall time, Mbit/s | Short tests include TCP slow start and read low |
| Mathis ratio | Measured Mbit/s divided by `MSS/RTT * 1.22/sqrt(p)` | The model assumes random loss and Reno-like TCP. Ratios far from 1 are normal on bursty paths or with CUBIC and BBR |

How each number is computed and where it can mislead: [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

## Safety and ethics

Lacuna sends traffic. Keep it polite.

| Probe | Default | Hard cap |
|---|---|---|
| ICMP | 5 pps | 20 pps |
| UDP | 20 pps | 200 pps |
| TCP connect | 2 per s | 10 per s |
| Throughput | 10 s | 60 s, one at a time |

- Caps are enforced in code. Going over needs `--unsafe-rate`, which prints a warning. Use it only on paths and servers you own.
- The server never amplifies. A reply is never larger than the request. Probes need a cookie from a handshake, so spoofed sources get nothing back. The server limits each source IP to 200 packets/s, 8 live sessions and 60 s idle sessions, and ignores packets over 1400 bytes.
- The default `monitor` targets are well known anycast resolvers at a low rate: 20 pings and 10 connects every 5 minutes, one 10 s download every 15 minutes.
- Only probe hosts you are allowed to probe. Do not point `monitor` at someone else's server, and do not run a public `lacuna server` without thinking about who can reach it.

## Docker

```bash
docker build -t lacuna .
docker run --rm lacuna doctor
docker run --rm -p 9400:9400/udp -p 9401:9401/tcp lacuna server
docker run --rm -v lacuna-data:/data lacuna ping 1.1.1.1 --count 20
```

If `ping` fails inside a container, add `--cap-add NET_RAW`.

## Roadmap

- Mouse support and charts in the app
- IPv6
- QUIC probe
- Prometheus exporter
- Windows support
- One-way delay with a clock-sync estimate

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run `make test` before sending a pull request.

## License

MIT. See [LICENSE](LICENSE).
