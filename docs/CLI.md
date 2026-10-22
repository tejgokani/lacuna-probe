# CLI reference

`lacuna COMMAND [options]`. `lacuna --version` and `lacuna COMMAND --help` work everywhere.

Exit codes: 0 success, 1 runtime failure (target down, ping missing), 2 usage or config error, 130 Ctrl+C.

## Options every command accepts

| Flag | Meaning |
|---|---|
| `--db FILE` | SQLite file. Default `$XDG_DATA_HOME/lacuna/lacuna.db` (`~/.local/share/lacuna/lacuna.db`). Also read from `$LACUNA_DB` |

## Options every probe command accepts

`ping`, `udp`, `tcp`, `throughput`.

| Flag | Meaning |
|---|---|
| `--profile NAME` | Label for the network you are on. Default: hostname |
| `--no-store` | Print only. Do not write to the database |
| `--json` | Print JSON with the sample and params instead of a table |
| `--unsafe-rate` | Allow rates above the hard caps, with a warning |

## demo

```bash
lacuna demo [-o lacuna-report.html] [--base-port 9400]
```

Starts a server and a loss simulator on localhost, runs 400-packet UDP probes at 0, 0.5, 1, 2, 5 and 10 % loss for a uniform and a bursty model (mean burst 4), runs a TCP throughput test through a delayed and rate-capped path, and writes the report. Uses `base-port` and `base-port + 1` for the server and `base-port + 10`, `base-port + 11` for the simulator. Writes to `demo.db` next to your real database, never to it.

## server

```bash
lacuna server [--bind 0.0.0.0] [--port 9400]
```

UDP echo on `--port`, TCP throughput on `--port + 1`. Open both in your firewall. Stop with Ctrl+C.

## ping

```bash
lacuna ping TARGET [--count 100] [--interval 0.2]
lacuna ping 1.1.1.1 --count 20
```

Uses the system `ping`. `--interval` is seconds between packets: 0.2 is 5 pps (default cap), 0.05 is 20 pps (hard cap). On Linux, `ping` itself only allows intervals under 0.2 s for root.

## udp

```bash
lacuna udp HOST:PORT [--count 500] [--pps 20] [--size 200]
lacuna udp 203.0.113.10:9400 --count 1000 --pps 50 --profile hotspot
```

Needs `lacuna server` at HOST:PORT. `--count` up to 10944, `--size` 32 to 1400 bytes, `--pps` up to 200. Reports total, uplink and downlink loss, RTT percentiles, jitter, duplicates, reordering, longest burst and the Gilbert-Elliott fit.

## tcp

```bash
lacuna tcp HOST:PORT [--count 50] [--interval 0.5]
lacuna tcp 1.1.1.1:443 --count 20
```

Times the TCP handshake. Failed connects count as loss. At most 10 per second.

## throughput

```bash
lacuna throughput HOST:PORT [--secs 10] [--streams 1] [--direction up|down]
lacuna throughput --url URL [--secs 10] [--streams 4]
lacuna throughput 203.0.113.10:9400 --direction up --streams 2
lacuna throughput --url "https://speed.cloudflare.com/__down?bytes=10000000"
```

`HOST:PORT` is the server's UDP port. The TCP test connects to PORT + 1. `--secs` up to 60. `--streams` up to 8 for a Lacuna server and 16 for a URL. Only one throughput test can run at a time on a machine.

## monitor

```bash
lacuna monitor [--config FILE] [--profile NAME]
```

Runs the jobs in the config on schedule until Ctrl+C or SIGTERM. Config lookup order: `--config`, then `~/.config/lacuna/lacuna.toml` if it exists, then built-in defaults (3 ICMP, 2 TCP connect, 1 HTTP download). A failing job is logged and retried at its next slot. See [examples/lacuna.toml](../examples/lacuna.toml). For a systemd user service see [examples/lacuna-monitor.service](../examples/lacuna-monitor.service).

Job keys:

| Key | Applies to | Default |
|---|---|---|
| `kind` | all | required: icmp, udp, tcp, throughput, http |
| `target` | all | required |
| `every` | all | `5m` |
| `count` | icmp, udp, tcp | 100, 500, 50 |
| `interval` | icmp, tcp | 0.2, 0.5 |
| `pps`, `size` | udp | 20, 200 |
| `secs`, `streams`, `direction` | throughput, http | 10, 1, down |

## tui

```bash
lacuna tui [--profile NAME]
```

Curses view that re-reads the database every 2 seconds. Shows the last hour, the latest result per target and sparklines. `q` quits. Without a terminal it prints one snapshot.

## report

```bash
lacuna report [--profile NAME ...] [--since 7d] [-o lacuna-report.html]
lacuna report --profile campus-wifi --profile hotspot --since 1d -o wifi-vs-hotspot.html
```

Repeat `--profile` to include several. `--since` takes `90m`, `12h`, `7d`, `2w`. The output is one HTML file with no external requests.

## compare

```bash
lacuna compare PROFILE_A PROFILE_B [--since 7d]
```

Terminal table: runs, packets, mean loss, p95 RTT, jitter, median throughput, longest burst, Gilbert-Elliott mean burst, and a verdict line per profile.

## export

```bash
lacuna export [--format csv|json] [--profile NAME ...] [--since 7d] [-o FILE]
lacuna export --format json --since 1d -o last-day.json
```

CSV is one flat table with a `sample` column (`loss` or `tput`) and empty cells where a column does not apply. JSON has `loss_samples` and `tput_samples` arrays. Stdout when `-o` is omitted.

## doctor

```bash
lacuna doctor
```

Checks Python version, TOML support, the `ping` binary, that the database can be opened and written, UDP loopback, and `TCP_INFO` availability. Exit 1 only when a required check fails. A missing `ping` or `TCP_INFO` is a warning.
