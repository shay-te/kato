#!/bin/sh
# POSIX wrapper around the canonical scripts/bootstrap.py. On Windows
# (cmd / PowerShell) operators run `python scripts\bootstrap.py` directly.
set -eu
cd "$(dirname "$0")/.."
exec python3 scripts/bootstrap.py "$@"
