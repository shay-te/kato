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
    'configure': (
        'Generate .env interactively',
        True,
        ['scripts/generate_env.py', '--output', '.env'],
    ),
    'doctor': (
        'Validate full env config',
        True,
        ['-m', 'kato.validate_env', '--env-file', '.env', '--mode', 'all'],
    ),
    'doctor-agent': (
        'Validate just the agent backend',
        True,
        ['-m', 'kato.validate_env', '--env-file', '.env', '--mode', 'agent'],
    ),
    'doctor-openhands': (
        'Validate just the openhands config',
        True,
        ['-m', 'kato.validate_env', '--env-file', '.env', '--mode', 'openhands'],
    ),
    'test': (
        'Run the unit-test suite',
        True,
        ['-m', 'unittest', 'discover', '-s', 'tests'],
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
    'approve-repo': (
        'Approve a repository for kato use (REP). '
        'Args: <repo_id> --remote <url> [--trusted]',
        True,
        ['scripts/approve_repository.py', 'approve'],
    ),
    'revoke-repo': (
        'Remove an entry from the REP approval list. Args: <repo_id>',
        True,
        ['scripts/approve_repository.py', 'revoke'],
    ),
    'list-approved-repos': (
        'List repositories on the REP approval list',
        True,
        ['scripts/approve_repository.py', 'list'],
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
    python = _resolve_python(prefer_venv=prefer_venv, repo_root=repo_root)
    cmd = [python, *base_args, *extra]
    try:
        return subprocess.call(cmd, cwd=str(repo_root))
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
