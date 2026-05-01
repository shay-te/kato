#!/bin/sh
# POSIX wrapper around the canonical scripts/run_local.py. On Windows
# (cmd / PowerShell) operators run `python scripts\run_local.py` directly.
set -eu
cd "$(dirname "$0")/.."
exec python3 scripts/run_local.py "$@"
