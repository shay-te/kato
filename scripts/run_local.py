"""Cross-platform replacement for ``scripts/run-local.sh``.

Loads ``.env``, hands the variables to the kato process via the parent
environment (no shell-only ``set -a; . .env``), and execs
``python -m kato.main`` from the project venv. Works on Windows, macOS,
and Linux.

Usage:
    python scripts/run_local.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _script_utils import (  # noqa: E402
    REPO_ROOT,
    load_env_file,
    venv_python_path,
)


def main() -> int:
    env_path = REPO_ROOT / '.env'
    if not env_path.exists():
        print('.env is missing. Run `python scripts/bootstrap.py` first.', file=sys.stderr)
        return 1

    python_bin = venv_python_path()
    if not python_bin.exists():
        print('.venv is missing. Run `python scripts/bootstrap.py` first.', file=sys.stderr)
        return 1

    env = os.environ.copy()
    env.update(load_env_file(env_path))

    completed = subprocess.run(
        [str(python_bin), '-m', 'kato.main'],
        cwd=REPO_ROOT,
        env=env,
    )
    return completed.returncode


if __name__ == '__main__':
    raise SystemExit(main())
