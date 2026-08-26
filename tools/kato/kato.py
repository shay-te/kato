"""Cross-platform mirror of the POSIX Makefile.

Single source of truth for the ``make.exe`` binary. PyInstaller
packages this file into a standalone Windows binary (see
``build.py`` next to this file); operators on Windows then type
``make <target>`` like macOS / Linux operators do.

When kato adds a new Makefile target, edit ``_TARGETS`` below and
rebuild ``make.exe`` (run ``python tools/make/build.py`` once).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# The dispatcher must work BEFORE the venv exists (``bootstrap``
# subcommand) — that's why we resolve ``kato_core_lib`` via the
# repo root rather than relying on it being on ``sys.path`` in a
# regular install.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# This file lives at ``<repo>/tools/make/make.py``, so the repo root
# is two parents up. PyInstaller-frozen builds use ``sys._MEIPASS``
# /the executable's location for resources, but for *running*
# subprocess commands we want the operator's actual cwd, which is
# the kato repo root they invoked ``make.exe`` from.
_FILE_REPO_ROOT = Path(__file__).resolve().parents[2]


def _runtime_repo_root() -> Path:
    """Where to run subprocess commands from.

    When frozen into make.exe, the operator runs the binary from
    inside their kato checkout — that's the cwd we want. When
    running this script directly from the repo, ``_FILE_REPO_ROOT``
    is correct because the script lives inside the repo. The CWD
    fallback covers both.
    """
    if getattr(sys, 'frozen', False):
        return Path.cwd()
    return _FILE_REPO_ROOT


_VENV_PYTHON_REL = Path('.venv') / ('Scripts' if os.name == 'nt' else 'bin') / (
    'python.exe' if os.name == 'nt' else 'python'
)


def _resolve_python(*, prefer_venv: bool, repo_root: Path) -> str:
    """System ``python`` for bootstrap (venv doesn't exist yet);
    venv python for everything else if the venv has been created.
    """
    if prefer_venv:
        candidate = repo_root / _VENV_PYTHON_REL
        if candidate.is_file():
            return str(candidate)
    # Frozen ``make.exe`` doesn't have ``sys.executable`` pointing at
    # a real Python — fall back to the OS-resolved ``python``.
    if getattr(sys, 'frozen', False):
        return 'python'
    return sys.executable if Path(sys.executable).name.lower().startswith('python') else 'python'


# (description, prefer_venv, argv) per target. ``prefer_venv=False`` means
# the target runs even before bootstrap (currently only ``bootstrap``
# itself).
_TARGETS: dict[str, tuple[str, bool, list[str]]] = {
    'bootstrap': (
        'Install Python deps + build the planning UI',
        False,
        ['scripts/bootstrap.py'],
    ),
    'doctor': (
        'Validate full env config',
        True,
        ['-m', 'kato.validate_env', '--mode', 'all'],
    ),
    'test': (
        'Run the unit-test suite (kato + every owned core-lib)',
        True,
        ['scripts/run_all_tests.py'],
    ),
    'up': (
        'Start kato',
        True,
        ['scripts/run_local.py'],
    ),
    'sandbox-build': (
        'Build the hardened Docker sandbox image',
        True,
        ['-c', 'from kato.sandbox.manager import build_image; build_image()'],
    ),
    'sandbox-verify': (
        'End-to-end smoke test of the sandbox',
        True,
        ['-m', 'kato.sandbox.verify'],
    ),
    'sandbox-login': (
        'Interactive Claude login inside the sandbox',
        True,
        [
            '-c',
            'from kato.sandbox.manager import ensure_image, login_command; '
            'import subprocess, sys; ensure_image(); '
            'sys.exit(subprocess.call(login_command()))',
        ],
    ),
    # ``approve-repo`` is gone — repository approvals now live in
    # the planning UI's Settings drawer (Approvals tab). The
    # backend uses the same RepositoryApprovalService, so any
    # ``~/.kato/approved-repositories.json`` written by the old CLI
    # keeps working unchanged.
    'history': (
        'Show the most recent kato task activity (numbered list). '
        'No flags — for fine-grained filtering, ``jq`` the audit '
        'log JSONL directly.',
        True,
        ['scripts/audit_log_query.py'],
    ),
}


def _print_usage(*, error: str = '') -> None:
    if error:
        print(f'{error}\n', file=sys.stderr)
    print('Usage: kato <target> [args...]')
    print()
    print('Targets:')
    width = max(len(name) for name in _TARGETS)
    for name, (desc, _venv, _argv) in _TARGETS.items():
        print(f'  {name.ljust(width)}  {desc}')


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ('-h', '--help', 'help'):
        _print_usage()
        return 0
    target = argv[0]
    extra = argv[1:]
    if target not in _TARGETS:
        _print_usage(error=f'Unknown target: {target!r}')
        return 1
    _desc, prefer_venv, base_args = _TARGETS[target]
    repo_root = _runtime_repo_root()
    # Load ``~/.kato/settings.json`` into the environment BEFORE we
    # hand off to the subcommand, so child scripts that consult
    # ``os.environ`` see the operator's saved config. Real shell env
    # vars still win. settings.json is kato's ONLY config file —
    # ``.env`` support was removed (the first-run wizard + Settings
    # drawer replaced it).
    try:
        from kato_core_lib.helpers.kato_settings_store_utils import (
            load_kato_settings_into_environ,
        )
        load_kato_settings_into_environ()
    except Exception:
        # Never let a settings.json problem block boot — validation
        # inside the subcommand will report what's wrong.
        pass
    python = _resolve_python(prefer_venv=prefer_venv, repo_root=repo_root)
    cmd = [python, *base_args, *extra]
    return _run_child(cmd, cwd=str(repo_root))


#: How long Ctrl+C waits for the child to finish its own graceful shutdown
#: before escalating. Slightly longer than the child's own grace period so
#: its cleanup gets to finish first.
CHILD_SHUTDOWN_GRACE_SECONDS = 10.0


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
