"""Help center: topics on the left, text on the right."""

from __future__ import annotations

from lacuna.tui.core import L, Line, Scroller, side_by_side, wrap_block
from lacuna.tui.services import Context

# Lines starting with "# " are headings. Indented lines and lines starting with "|" are not wrapped.
TOPICS: list[tuple[str, str]] = [
    ("Welcome", """\
# Welcome to Lacuna

Lacuna measures real packet loss, jitter, RTT and throughput on a live network path, keeps the history, and tells you whether loss is random or bursty.

# Start here

  1  Dashboard  what you have measured so far
  2  Probes     run one test right now
  3  Monitor    keep testing in the background
  4  Server     be the far end for UDP and throughput tests
  5  Report     HTML report and CSV or JSON export
  6  Settings   profile name, folders, refresh rate
  7  Help       this screen

New here? Go to Probes (2), pick "Offline demo", press Enter. In about 40 seconds you have a full set of simulated data. Then press 1 and d to look at it.

For real numbers: pick "Ping", leave the defaults, press r.

Use the Up and Down arrows to pick a topic on the left. PgUp and PgDn scroll the text."""),
    ("Keys", """\
# Keys that work everywhere

  1 to 7        jump to a screen
  Tab, Shift-Tab  next or previous screen
  ? or F1       open this help
  q             quit (stops the monitor and server cleanly)
  Ctrl-C        quit

# While typing in a field

  Enter         accept
  Esc           cancel
  Left, Right, Home, End, Backspace, Delete work as expected

# Per screen

  Dashboard   p profile, w time window, d demo data, r refresh
  Probes      up/down field, Enter edit, r run, left/right choose
  Monitor     s start or stop, l reload config
  Server      s start or stop
  Report      Space toggle profile, a all, w window, r HTML, c CSV, j JSON, o open
  Settings    up/down, Enter to edit"""),
    ("Probes explained", """\
# Which probe should I use?

| Probe       | Needs  | Tells you                                     |
| Ping        | none   | loss and RTT to any host, via ICMP            |
| TCP connect | none   | handshake time, refused or timed-out connects |
| UDP         | server | loss by direction (up or down), bursts        |
| Throughput  | server | TCP speed up or down, retransmits on Linux    |
| HTTP        | none   | download speed from a web server              |

"server" means a running Lacuna server, see the Setting up a server topic.

# Why UDP needs a server

Plain ping cannot tell you which direction lost the packet. The Lacuna server counts what it received and reports it back, so the loss splits into uplink (you to server) and downlink (server to you).

# Ping and routers

Many routers slow down or drop ICMP. Ping loss can be higher or lower than what your real traffic sees. UDP loss is usually closer.

# Throughput tests

They fill the path for their whole duration, which can raise delay and loss for everything else. Do not read loss measured during a throughput test as idle loss."""),
    ("Reading the numbers", """\
# What the numbers mean

| Number        | Meaning                                              |
| Loss %        | share of probes that never came back                 |
| Burst         | consecutive lost packets in a row                    |
| Mean burst    | 1 / r in the Gilbert-Elliott fit. Near 1 = random    |
| RTT p50/p95   | typical and worst-ish round trip time                |
| Jitter        | smoothed change in RTT between replies (RFC 3550)    |
| Mathis ratio  | measured speed / model speed for that loss and RTT   |

# Colors on the Dashboard

Loss under 1 % is green, 1 to 5 % yellow, above 5 % red.

# Verdict line

"Bursty loss" means the runs test found loss clumpier than independent drops. "Loss looks random" only means that no clumping was proven. Under 10 lost packets, no verdict is given.

# Sample size

500 packets at 1 % loss is only about 5 events. Treat small samples as rough. The Gilbert-Elliott fit is flagged when it uses fewer than 200 packets.

Full details: docs/METHODOLOGY.md in the repository."""),
    ("Safety and limits", """\
# Rate caps

| Probe       | Default   | Hard cap            |
| ICMP        | 5 pps     | 20 pps              |
| UDP         | 20 pps    | 200 pps             |
| TCP connect | 2 per s   | 10 per s            |
| Throughput  | 10 s      | 60 s, one at a time |

The app refuses anything above the hard caps. The CLI can go higher with --unsafe-rate, only on paths and servers you own.

# The server never amplifies

A reply is never larger than the request. Probes need a handshake cookie. Each source IP gets 200 packets per second and 8 live sessions.

# Be polite

Only probe hosts you are allowed to probe. Do not leave a public server running without thinking about who can reach it."""),
    ("Setting up a server", """\
# Two machines, one path

1. On the far end (a cloud VM or a friend's computer), run the Server screen or:

    lacuna server

2. Open UDP 9400 and TCP 9401 in its firewall.

3. On your machine, open Probes, choose "UDP probe", enter ADDRESS:9400 and press r.

# Testing on one machine

Start the server on screen 4, then use 127.0.0.1:9400 as the target. This only tests your own computer, not a network.

# Changing ports

Settings, "Server port". TCP always uses the UDP port plus one."""),
    ("Monitoring all day", """\
# Monitor screen

Runs a list of probe jobs on a schedule and stores every result. Leave the app open, or use the command line version in a terminal or as a service:

    lacuna monitor --profile campus-wifi

# Profiles

A profile is a label for the network you are on: campus-wifi, hotspot, home-fiber. Change it in Settings before you switch networks. Compare profiles on the Report screen or with:

    lacuna compare campus-wifi hotspot

# Jobs

By default: ping 1.1.1.1, 8.8.8.8 and 9.9.9.9, TCP connects to two of them, and one HTTP download every 15 minutes. To change that, copy examples/lacuna.toml to ~/.config/lacuna/lacuna.toml and edit it. Press l on the Monitor screen to reload."""),
    ("Files and folders", """\
# Where things live

| What            | Where                                          |
| Measurements    | ~/.local/share/lacuna/lacuna.db                |
| Demo data       | ~/.local/share/lacuna/demo.db                  |
| App settings    | ~/.config/lacuna/settings.json                 |
| Monitor config  | ~/.config/lacuna/lacuna.toml (optional)        |
| Reports         | the folder set in Settings (default: current)  |

The paths follow XDG_DATA_HOME and XDG_CONFIG_HOME when those are set.

The demo never writes to your real database."""),
    ("Troubleshooting", """\
# "ping not found"

Install it. Ubuntu: sudo apt install iputils-ping. Run lacuna doctor for a full check.

# "no HELLO_ACK"

The UDP probe could not reach a Lacuna server. Check the address, that the server is running, and that the UDP port is open in the firewall.

# The demo says a port is in use

The demo needs the server port and the next 11 ports free. Stop the server on screen 4 or change the port in Settings.

# Throughput retrans and cwnd are empty

Those need Linux TCP_INFO. On other systems the columns stay empty.

# Colors look wrong

Turn colors off in Settings. Make sure your terminal is at least 70 columns by 20 rows.

# Something else

Run lacuna doctor, then open an issue with its output."""),
    ("About", """\
# Lacuna

A lacuna is a gap, here the missing packets.

Real measurements, stored in SQLite, shown in one self-contained HTML report. Zero runtime dependencies, no root needed.

Commands without this app:

    lacuna --help

License: MIT."""),
]


def render_topic(text: str, width: int) -> list[Line]:
    out: list[Line] = []
    for raw in wrap_block(text, width):
        if raw.startswith("# "):
            if out and "".join(t for t, _ in out[-1]).strip():
                out.append(L())
            out.append(L(raw[2:], "hdr"))
        elif raw.startswith("|"):
            out.append(L(raw, "dim" if "---" in raw else ""))
        else:
            out.append(L(raw))
    while out and not "".join(t for t, _ in out[0]).strip():
        out.pop(0)
    return out


class HelpScreen:
    name = "help"
    title = "Help"
    captures_text = False
    hints = "up/down topic  pgup/pgdn scroll text"

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.topic = 0
        self.scroll = Scroller()
        self._total = self._h = 0

    def on_show(self) -> None:
        pass

    def tick(self, now: float) -> None:
        pass

    def key(self, k: str) -> None:
        if k == "up":
            self.topic = max(0, self.topic - 1)
            self.scroll.top = 0
        elif k == "down":
            self.topic = min(len(TOPICS) - 1, self.topic + 1)
            self.scroll.top = 0
        else:
            self.scroll.key(k, self._total, self._h)

    def render(self, w: int, h: int) -> list[Line]:
        left_w = min(24, max(16, w // 4))
        menu: list[Line] = [L(" Help center", "hdr"), L()]
        for i, (name, _) in enumerate(TOPICS):
            menu.append([("> " if i == self.topic else "  ", "key"), (name, "sel" if i == self.topic else "")])
        body_w = max(30, w - left_w - 4)
        body = render_topic(TOPICS[self.topic][1], body_w)
        self._total, self._h = len(body), h
        view = self.scroll.view(body, h)
        if len(body) > h:
            pos = f" {self.scroll.top + 1}-{min(self.scroll.top + h, len(body))}/{len(body)} "
            view = view[:-1] + [[(pos.rjust(body_w), "dim")]] if view else view
        return side_by_side(menu, view, left_w)[:h]
