"""``./kato`` (and ``kato.exe`` on Windows): runs kato's one CLI.

Every subcommand lives in ``kato_core_lib/cli.py``; this only finds a Python
to run it with and starts it from the repository. There used to be a second
list of commands here, and it drifted: ``doctor`` and the sandbox commands
named modules that no longer existed, so ``./kato doctor`` — the first thing
the README tells a new user to run — crashed.

It has to work before a virtualenv exists (``bootstrap``, and ``up``, which
bootstraps on first run) and when frozen into ``kato.exe`` by PyInstaller
(``build.py`` next to this file), where ``sys.executable`` is the binary
itself, not a Python. The CLI module imports nothing outside the standard
library, so any Python 3.11+ can start it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# ``kato_core_lib`` is resolved from the repository, not an install: the
# settings loader below runs before any virtualenv exists.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# This file lives at ``<repo>/tools/kato/kato.py``, so the repo root is two
# parents up. A frozen ``kato.exe`` is run from inside the operator's kato
# checkout, so there the current directory is the repository.
_FILE_REPO_ROOT = Path(__file__).resolve().parents[2]


def _runtime_repo_root() -> Path:
    """Where the CLI runs from: the checkout this file is in, or — frozen —
    the checkout ``kato.exe`` was started in."""
    if getattr(sys, 'frozen', False):
        return Path.cwd()
    return _FILE_REPO_ROOT


_VENV_PYTHON_REL = Path('.venv') / ('Scripts' if os.name == 'nt' else 'bin') / (
    'python.exe' if os.name == 'nt' else 'python'
)

# Run with the system Python even when a virtualenv exists: ``bootstrap``
# (re)creates that virtualenv.
_SYSTEM_PYTHON_COMMANDS = frozenset({'bootstrap'})

# The names these commands had before they joined the CLI.
_ALIASES = {
    'sandbox-build': ['sandbox', 'build'],
    'sandbox-verify': ['sandbox', 'verify'],
    'sandbox-login': ['sandbox', 'login'],
}

CLI_MODULE = 'kato_core_lib.cli'


def _resolve_python(*, prefer_venv: bool, repo_root: Path) -> str:
    """The virtualenv's Python when there is one (and it is wanted), else a
    Python on PATH: a frozen ``kato.exe``'s ``sys.executable`` is not one."""
    if prefer_venv:
        candidate = repo_root / _VENV_PYTHON_REL
        if candidate.is_file():
            return str(candidate)
    if getattr(sys, 'frozen', False):
        return 'python'
    return sys.executable if Path(sys.executable).name.lower().startswith('python') else 'python'


def cli_command(argv: list[str], *, repo_root: Path) -> list[str]:
    """The command that runs the CLI with ``argv``."""
    args = list(argv)
    if args and args[0] in _ALIASES:
        args = _ALIASES[args[0]] + args[1:]
    prefer_venv = not (args and args[0] in _SYSTEM_PYTHON_COMMANDS)
    python = _resolve_python(prefer_venv=prefer_venv, repo_root=repo_root)
    return [python, '-m', CLI_MODULE, *args]


def main(argv: list[str]) -> int:
    repo_root = _runtime_repo_root()
    # Load ``~/.kato/settings.json`` into the environment BEFORE handing off,
    # so the subcommand sees the operator's saved config. Real shell env vars
    # still win. settings.json is kato's ONLY config file.
    try:
        from kato_core_lib.helpers.kato_settings_store_utils import (
            load_kato_settings_into_environ,
        )
        load_kato_settings_into_environ()
    except Exception:
        # Never let a settings.json problem block boot — validation inside
        # the subcommand reports what's wrong.
        pass
    return _run_child(cli_command(argv, repo_root=repo_root), cwd=str(repo_root))


#: How long Ctrl+C waits for the child to finish its own graceful shutdown
#: before escalating. Longer than kato's own worst case — its cleanup budget
#: (8s) plus the bounded log flush (2s) — so that cleanup always gets to
#: finish first. At exactly 10s the two could tie, and this wrapper would
#: terminate the launcher in the last instant of kato's shutdown.
CHILD_SHUTDOWN_GRACE_SECONDS = 15.0


def _run_child(cmd, *, cwd):
    """Run the real command, and do not return while it is still alive.

    ``subprocess.call`` + ``except KeyboardInterrupt`` ORPHANS the child. The
    terminal delivers SIGINT to the whole foreground process group, so both
    processes get it — but this wrapper raised KeyboardInterrupt immediately
    and returned, handing back a shell prompt while kato was still running.
    The operator's report was "I can't stop kato with Ctrl+C": the prompt
    came back, so it looked like it had stopped, and it had not.

    Ctrl+C now waits for the child to exit on its own, escalates to
    terminate, then to kill. A second Ctrl+C skips straight to the kill.
    """
    child = subprocess.Popen(cmd, cwd=cwd)
    while True:
        try:
            return child.wait()
        except KeyboardInterrupt:
            # The child received the same SIGINT from the terminal, so give
            # its own shutdown a chance before forcing anything.
            try:
                return child.wait(timeout=CHILD_SHUTDOWN_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            except KeyboardInterrupt:
                # A second Ctrl+C while waiting: stop being patient.
                pass
            child.terminate()
            try:
                return child.wait(timeout=5)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                child.kill()
                child.wait()
                return 130


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
