"""Shared helpers for the cross-platform Python entry-point scripts.

Each top-level command script (`bootstrap.py`, `run_local.py`, ...) imports
from here so we keep one canonical implementation of: where the project
root lives, where the venv interpreter sits on the current OS, how to
invoke a subprocess with a clean failure surface, and how to load .env.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = REPO_ROOT / '.venv'


def venv_python_path() -> Path:
    """Return the path to the venv Python interpreter on the current OS.

    POSIX: ``.venv/bin/python``. Windows: ``.venv\\Scripts\\python.exe``.
    Returns the path even if the venv doesn't exist yet, so callers can
    test ``.exists()`` themselves and decide how to react.
    """
    if os.name == 'nt':
        return VENV_DIR / 'Scripts' / 'python.exe'
    return VENV_DIR / 'bin' / 'python'


def run_step(label: str, command: list[str], **kwargs) -> None:
    """Echo a step and run it, exiting with the step's exit code on failure.

    Mirrors the run_step shell function from the original ``bootstrap.sh``
    so the operator-visible output is the same on every platform.
    """
    print(f'==> {label}', flush=True)
    try:
        subprocess.run(command, check=True, cwd=REPO_ROOT, **kwargs)
    except subprocess.CalledProcessError as exc:
        print(f'Step failed: {label}', file=sys.stderr)
        print(
            f'Fix the error above and rerun this script.',
            file=sys.stderr,
        )
        sys.exit(exc.returncode or 1)
    except FileNotFoundError as exc:
        print(f'Step failed: {label} ({exc})', file=sys.stderr)
        sys.exit(1)


def have_executable(name: str) -> bool:
    """True when ``name`` is on PATH. Cross-platform via shutil.which."""
    from shutil import which

    return which(name) is not None


def load_env_file(env_path: Path) -> dict[str, str]:
    """Parse a ``KEY=VALUE`` ``.env`` file into a dict.

    Accepts the subset every kato consumer uses: blank lines, ``#``
    comments, ``KEY=VALUE`` pairs with optional surrounding double or
    single quotes. We don't shell-out to ``set -a; . .env`` because that
    only works on POSIX; this parser is the cross-platform equivalent.
    """
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        values[key] = value
    return values
