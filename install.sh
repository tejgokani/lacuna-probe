#!/usr/bin/env bash
# Install Lacuna into its own venv and link `lacuna` into ~/.local/bin.
# No root needed. Re-run to upgrade.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
venv="$data_home/lacuna/venv"
bin_dir="$HOME/.local/bin"

say()  { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || die "python3 not found. Ubuntu: sudo apt install python3 python3-venv"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python 3.10 or newer is required (found $(python3 -V 2>&1))"

say "creating venv at $venv"
python3 -m venv "$venv" 2>/dev/null \
  || die "could not create a venv. Ubuntu: sudo apt install python3-venv"

say "installing lacuna-probe"
"$venv/bin/python" -m pip install --quiet --disable-pip-version-check "$here" \
  || die "pip install failed"

mkdir -p "$bin_dir"
ln -sf "$venv/bin/lacuna" "$bin_dir/lacuna"
say "linked $bin_dir/lacuna"

if ! command -v ping >/dev/null 2>&1; then
  warn "ping not found, ICMP probes will not work. Ubuntu: sudo apt install iputils-ping"
fi
case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) warn "$bin_dir is not on PATH. Add this to your shell profile: export PATH=\"$bin_dir:\$PATH\"" ;;
esac

say ""
"$bin_dir/lacuna" doctor
say ""
say "next: lacuna demo"
