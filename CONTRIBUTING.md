# Contributing

Thanks for helping. Lacuna is small on purpose.

## Setup

```bash
git clone https://github.com/tejgokani/lacuna-probe.git
cd lacuna-probe
make install          # venv in .venv, editable install
make test
make demo             # rebuilds examples/sample-report/index.html
```

`make test` uses `unittest` only. Tests that open sockets use ports 9420 to 9469 on localhost. They set `XDG_DATA_HOME` to a temp directory where it matters, so they never touch your real database.

## Rules of the project

| Rule | Why |
|---|---|
| Python standard library only at runtime | Install must work on a bare Ubuntu box. `tomli` on 3.10 is the one exception |
| No root, no raw sockets | ICMP goes through the system `ping` |
| Respect the rate caps in `config.py` | Lacuna must stay safe to point at the internet |
| The server never sends more than it received | No amplification |
| Charts are hand-written inline SVG | No JS, no CDN, report works offline |
| Type hints, dataclasses, small modules, no function over 60 lines | Keep it readable |
| `ruff check src tests` is clean if you have ruff | CI runs it |

## Writing style

In docs and comments: short direct sentences, no filler, no marketing tone, no em dashes. Prefer a table or an ASCII diagram to a paragraph.

## Making a change

1. Open an issue first for anything larger than a bug fix.
2. Write the test with the change. Pure functions go in `tests/test_*.py` next to their module's name. Network code gets a loopback test.
3. If you change a metric, update `docs/METHODOLOGY.md`. If you change a flag, update `docs/CLI.md`.
4. Use Conventional Commits: `feat:`, `fix:`, `test:`, `docs:`, `ci:`, `chore:`.
5. Run `make test`, and `make demo` if the report changed.

## Checks CI runs

- `make test` on Python 3.10, 3.11, 3.12
- `make demo`, then the report must contain no URL outside the methodology footnote
- no em dash character anywhere in the repo
- `ruff check src tests`

## Reporting problems

Use the issue templates. For security issues see [SECURITY.md](SECURITY.md).
