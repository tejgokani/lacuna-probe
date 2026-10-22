# Architecture

## Components

```
 CLI (cli.py)
   |
   |  one-shot probe commands           long running
   v                                    v
 monitor.run_job() <------------------ monitor.Monitor (scheduler, config.py jobs)
   |
   +--> probes/icmp.py        system `ping`, parse output
   +--> probes/udp.py   <---> server.py (UDP echo)       protocol.py wire format
   +--> probes/tcp.py         connect timer
   +--> probes/throughput.py <-> server.py (TCP sink/source), http.client
   |
   v  LossSample / TputSample
 monitor.store() ---> db.py (SQLite)
                         |
          +--------------+------------------+
          v              v                  v
   report/html.py     tui/ (app)        export.py
   report/svg.py
   analysis/*  (stats, gilbert, mathis): pure functions used by probes and report
```

```
 lacuna (no arguments, on a terminal)
   |
   v
 tui/app.py  App: tab bar, status bar, key routing  -- curses driver is the only part that touches curses
   |
   +-- dashboard.py        reads the database, profile_summary(), sparklines
   +-- probes.py           form -> build_job() -> monitor.run_job() + store() on a worker Task
   +-- monitor_server.py   screens for the two background services
   +-- report_settings.py  report.html / export, settings form
   +-- help_screen.py      help center topics
   +-- services.py         Context (shared state), MonitorController, ServerController
   +-- settings.py         JSON settings, validation
   +-- core.py             Line/Seg types, Form, Editor, Scroller, Task (no curses import)
```

Screens render to lists of styled segments and receive key names as strings, so the whole app is tested without a terminal. Only `app.py` imports `curses`, inside its driver functions.

`lossim.py` and `demo.py` are test and demo helpers. They are never imported by the probes.

## Data flow of one UDP run

```
client                          server
  |--- HELLO (>= 48 B) --------->|   server creates session, cookie = HMAC(secret, ip, port, session)
  |<-- HELLO_ACK(cookie, 32 B) --|
  |--- PROBE seq=0 ------------->|   cookie in the first 8 payload bytes, t_send_ns in header
  |<-- ECHO  seq=0 --------------|   same length as the probe
  |        ... N probes at --pps ...
  |   wait --grace seconds for stragglers
  |--- STATS_REQ (cookie) ------>|   padded so the reply fits inside the request size
  |<-- STATS(rx, max_seq, bitmap)|
  |
  loss bits = seq with no ECHO
  uplink loss   = (sent - server rx) / sent
  downlink loss = (server rx - echoed) / server rx
```

Wire format and server rules: see the docstrings in `protocol.py` and `server.py`.

## Modules

| Module | Job |
|---|---|
| `protocol.py` | Header pack and unpack, cookies, STATS bitmap |
| `server.py` | `ServerState` (pure packet logic, testable with an injected clock), `LacunaServer` (sockets and threads) |
| `probes/*` | One module per probe. Each returns a dataclass ready for the database |
| `analysis/stats.py` | Percentiles, RFC 3550 jitter, hex bitstrings, burst runs, runs test |
| `analysis/gilbert.py` | Two-state loss model fit and simulation |
| `analysis/mathis.py` | Mathis formula and sample pairing |
| `db.py` | Schema, forward-only migrations, inserts, row queries |
| `config.py` | TOML, defaults, rate caps, XDG paths |
| `monitor.py` | `run_job`, `store`, and the scheduler loop with clean SIGINT and SIGTERM exit |
| `report/svg.py` | Chart helpers. They emit CSS classes, not colors |
| `report/html.py` | Aggregation, verdict logic, page assembly |
| `lossim.py` | UDP loss simulator (uniform and Gilbert), TCP delay and rate proxy |
| `demo.py` | Orchestrates server, simulator and report for `lacuna demo` |

## Storage

```
meta(key, value)                       schema_version
runs(id, ts, profile, kind, target, params_json)
loss_samples(run_id -> runs, sent, recv, loss_pct, up/down loss, rtt_*, jitter_ms,
             dup, reordered, max_burst, ge_p, ge_r, loss_bits_hex)
tput_samples(run_id -> runs, direction, streams, secs, bytes, mbps,
             retrans, cwnd, rtt_ms, ssthresh)
```

`loss_bits_hex` holds one bit per packet, most significant bit first, 1 = lost. The report reads it to redo burst analysis, so new analysis never needs new measurements.

## Threads

- `lacuna server`: one UDP thread, one TCP accept thread, one short-lived thread per TCP connection (max 16).
- `lacuna` app: the UI loop is one thread. Probes, the demo, the monitor and the server each run on their own thread and never touch the screen. The UI polls them.
- `lacuna monitor`: single thread, jobs run one at a time. A throughput test never overlaps another probe.
- Throughput tests also take a lock file in the data directory, so two processes cannot run one at once.
