# Changelog

## 0.2.0

- Bare `lacuna` opens a full-screen terminal app: Dashboard, Probes, Monitor, Server, Report, Settings and a Help center
- Run any probe or the offline demo from forms, start and stop the monitor and server in the background, build reports and exports, edit and save settings
- `lacuna tui` is the same app, and prints one snapshot when there is no terminal
- Fix: `lacuna server` leaked its UDP socket when the TCP port was already in use
- Clearer verdict for runs shorter than 50 packets
- Probe result formatting moved to `fmt.py` and is shared by the CLI and the app

## 0.1.0

First release.

- UDP probe with a small handshake, per-direction loss from server stats, RFC 3550 jitter,
  loss burst runs and a Gilbert-Elliott fit
- ICMP probe through the system `ping`, TCP connect probe
- TCP throughput test against `lacuna server` and an HTTP download test, with `TCP_INFO` on Linux
- Mathis model check for throughput samples that have a paired loss and RTT measurement
- SQLite storage with forward-only migrations, loss sequences kept as hex bitstrings
- `lacuna monitor` scheduler, `lacuna tui` live view, CSV and JSON export
- Single-file HTML report with inline SVG charts, light and dark mode
- `lacuna demo`: offline run through a loss simulator
- Server anti-abuse rules: no amplification, cookie check, per-IP rate and session caps
- Rate caps in code, `--unsafe-rate` to override
