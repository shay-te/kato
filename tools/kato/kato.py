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


def _load_env_file_into_environ(env_path: Path) -> int:
    """Read ``KEY=VALUE`` lines from ``env_path`` into ``os.environ``.

    Real env vars win — values already present in the parent
    environment are NOT overwritten, so an operator who sets
    ``KATO_WORKSPACES_ROOT`` in their shell still wins over a stale
    line in ``.env``. Returns the number of new keys actually added.

    Why this is in the dispatcher and not the individual scripts:
    every kato CLI subcommand needs the same env (``REPOSITORY_ROOT_PATH``,
    ``KATO_WORKSPACES_ROOT``, ``KATO_CONFIG``, ``BITBUCKET_*``, etc.).
    Putting the loader here means scripts under ``scripts/`` can keep
    using ``os.environ.get(...)`` and Just Work whether they're
    invoked through ``./kato`` / ``kato.exe`` from any directory.

    Best-effort: a malformed line is skipped, not raised. Operators
    have hit "kato won't start because line 47 of .env has a stray
    quote" before; we'd rather load 23 of 24 valid lines than fail
    the whole dispatcher.
    """
    if not env_path.is_file():
        return 0
    try:
        text = env_path.read_text(encoding='utf-8')
    except OSError:
        return 0
    added = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        # Strip an optional ``export `` prefix so files written for
        # ``source .env`` Bash usage still parse here.
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        if '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # Drop a single matched pair of surrounding quotes — covers
        # both ``KEY='value'`` and ``KEY="value"`` styles. Embedded
        # quotes are preserved.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key in os.environ:
            continue
        os.environ[key] = value
        added += 1
    return added

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
        'Manage the REP approval list. '
        'No args = unified picker that shows every repo (from your '
        'kato config, your kato workspaces, AND your '
        'REPOSITORY_ROOT_PATH checkouts). Approved repos start '
        'pre-checked; type indices like ``1,3,5-7`` to toggle, '
        'press Enter to apply. One command for add+edit+remove. '
        'Scripted form (CI): ``approve-repo approve <id> --remote '
        '<url> [--trusted]``, ``approve-repo revoke <id>``, '
        '``approve-repo list``.',
        True,
        ['scripts/approve_repository.py'],
    ),
    'revoke-repo': (
        'Remove an entry from the REP approval list (scripted '
        'form). Args: ``<repo_id>``. For interactive use prefer '
        '``approve-repo`` — that picker handles add+edit+remove '
        'in one screen.',
        True,
        ['scripts/approve_repository.py', 'revoke'],
    ),
    'list-approved-repos': (
        'Print the REP approval list (scripted form). For '
        'interactive use prefer ``approve-repo`` — its picker '
        'shows the same list with ``[x]`` markers and lets you '
        'edit it in place.',
        True,
        ['scripts/approve_repository.py', 'list'],
    ),
    'history': (
        'Show recent kato task activity. '
        'Options: --last N, --task <id>, --failed',
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
    # Load ``<repo_root>/.env`` into the environment BEFORE we hand
    # off to the subcommand. Without this, scripts like
    # ``approve_repository.py`` that consult ``os.environ`` get the
    # bare shell environment — which on Windows almost never
    # carries kato's vars — and default to ``~/.kato/workspaces``,
    # silently scoping to the wrong location. Real env vars still
    # win over ``.env``; see ``_load_env_file_into_environ`` above.
    _load_env_file_into_environ(repo_root / '.env')
    python = _resolve_python(prefer_venv=prefer_venv, repo_root=repo_root)
    cmd = [python, *base_args, *extra]
    try:
        return subprocess.call(cmd, cwd=str(repo_root))
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
