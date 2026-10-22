# Changelog

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
