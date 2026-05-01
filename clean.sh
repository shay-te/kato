#!/bin/sh
# POSIX wrapper around scripts/clean.py. On Windows operators run
# `python scripts\clean.py` directly.
set -eu
cd "$(dirname "$0")"
exec python3 scripts/clean.py "$@"
