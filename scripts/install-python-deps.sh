#!/bin/sh
# POSIX wrapper around scripts/install_python_deps.py. On Windows
# operators run `python scripts\install_python_deps.py` directly.
set -eu
cd "$(dirname "$0")/.."
exec python3 scripts/install_python_deps.py "$@"
