# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | yes |

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Use GitHub's private reporting: open the **Security** tab of this repository and choose **Report a vulnerability**. If that button is not available, open a public issue titled "security contact request" with no details, and the maintainer will arrange a private channel.

Include the version (`lacuna --version`), the command or packet that triggers the problem, and what you expected.

You can expect an acknowledgement within a few days. This is a small volunteer project, so there is no fixed fix time, but confirmed issues are handled before feature work.

## What counts

Things that matter for a network measurement tool:

- The server answering with more bytes than it received (amplification)
- Getting a reply from `lacuna server` without the handshake cookie
- Going past the per-IP packet, session or connection limits
- Memory or CPU growth in `lacuna server` from crafted packets
- Parsing bugs in `lacuna ping` output handling that run commands or write files
- Anything in the HTML report that executes script (the report is meant to contain none)

## Design notes

- `lacuna server` rules: replies never exceed requests, probes need an HMAC cookie bound to source address and session, 200 packets/s and 8 live sessions per source IP, 60 s idle expiry, 1400 byte limit, TCP limited to 16 connections and 60 s per transfer.
- The tool runs `ping` with a fixed argument list and no shell. The target is passed as a single argument.
- The server has no authentication. Anyone who can reach its ports can use it for echo and throughput tests, within those limits. Firewall it if that is not what you want.
