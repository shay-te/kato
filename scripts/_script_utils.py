"""Shared helpers for the cross-platform Python entry-point scripts.

Each top-level command script (`bootstrap.py`, `run_local.py`, ...) imports
from here so we keep one canonical implementation of: where the project
root lives, where the venv interpreter sits on the current OS, how to
invoke a subprocess with a clean failure surface, and how to load .env.
"""

from __future__ import annotations

import json
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
    so the operator-visible output is the same on every platform. Callers
    can override ``cwd`` via kwargs (e.g. the planning-UI npm steps run
    with ``cwd=webserver/ui`` on Windows where ``npm --prefix`` is
    unreliable through cmd.exe).
    """
    print(f'==> {label}', flush=True)
    kwargs.setdefault('cwd', REPO_ROOT)
    try:
        subprocess.run(command, check=True, **kwargs)
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


def read_kato_settings_file() -> dict[str, str]:
    """``~/.kato/settings.json`` (or ``$KATO_SETTINGS_FILE``) — the flat
    ``{"KEY": "value"}`` file the planning-UI Settings drawer writes.

    A deliberate stdlib-only mirror of
    ``kato_core_lib.helpers.kato_settings_store_utils.read_kato_settings``:
    ``run_local.py`` must honor UI-saved settings WITHOUT importing
    kato_core_lib, because its interpreter may not be the venv that has it.
    Tolerant — returns ``{}`` on a missing / corrupt / non-object file so a
    hand-edit typo can't brick ``kato up`` (same contract as the canonical
    reader).
    """
    override = os.environ.get('KATO_SETTINGS_FILE', '').strip()
    path = Path(override) if override else Path.home() / '.kato' / 'settings.json'
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if k}


def layered_env(base_env, *sources) -> dict[str, str]:
    """Compose a process env: ``base_env`` (the real shell) wins, then each
    ``source`` fills only keys still unset — earlier sources beat later ones.

    kato's documented precedence is
    ``layered_env(os.environ, read_kato_settings_file(), load_env_file(.env))``
    → **shell > ~/.kato/settings.json > <repo>/.env**. (``kato up`` used to
    ``env.update(.env)`` — loading ``.env`` OVER the shell AND never reading
    settings.json — so a value saved through the Settings UI had no effect.)
    """
    env = dict(base_env)
    for source in sources:
        for key, value in (source or {}).items():
            env.setdefault(str(key), str(value))
    return env
