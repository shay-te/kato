"""Cross-platform replacement for ``scripts/bootstrap.sh``.

Creates ``.venv``, installs kato + the webserver in editable mode, builds
the React planning UI bundle when ``npm`` is available, and runs the test
suite. Works on Windows, macOS, and Linux.

Usage:
    python scripts/bootstrap.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _script_utils import (  # noqa: E402
    REPO_ROOT,
    VENV_DIR,
    have_executable,
    run_step,
    venv_python_path,
)


def _ensure_env_file() -> None:
    env_path = REPO_ROOT / '.env'
    if env_path.exists():
        return
    template = REPO_ROOT / '.env.example'
    if not template.exists():
        print('.env.example missing; cannot create .env automatically.', file=sys.stderr)
        return
    shutil.copyfile(template, env_path)
    print('Created .env from .env.example')


def _ensure_venv() -> None:
    python_bin = venv_python_path()
    if python_bin.exists():
        return
    run_step('python -m venv .venv', [sys.executable, '-m', 'venv', str(VENV_DIR)])


def _install_python_deps() -> None:
    python_bin = str(venv_python_path())
    run_step(
        'install python deps (editable)',
        [
            sys.executable,
            str(REPO_ROOT / 'scripts' / 'install_python_deps.py'),
            python_bin,
            'editable',
        ],
    )
    run_step(
        'pip install -e ./webserver',
        [
            python_bin, '-m', 'pip', 'install', '--no-cache-dir',
            '-e', str(REPO_ROOT / 'webserver'),
        ],
    )


def _maybe_build_ui_bundle() -> None:
    if not have_executable('npm'):
        print('==> skipping webserver/ui build (npm not found; using committed bundle)')
        return
    ui_dir = REPO_ROOT / 'webserver' / 'ui'
    # Drop ``npm --prefix <path>`` and use ``cwd=ui_dir`` instead.
    # ``--prefix`` is unreliable on Windows: npm.cmd parses the prefix
    # path through cmd.exe quoting and ends up looking for
    # ``<repo_root>\package.json`` instead of ``<repo_root>\webserver
    # \ui\package.json``. ``cwd`` is honored uniformly across platforms.
    npm_args_install = ['npm', 'install', '--no-audit', '--no-fund']
    npm_args_build = ['npm', 'run', 'build']
    # Windows: ``npm`` is delivered as ``npm.cmd``; subprocess.run can't
    # find it without ``shell=True``. shutil.which already confirmed npm
    # is on PATH; we just need cmd.exe to resolve the right shim.
    use_shell = sys.platform == 'win32'
    run_step(
        'npm install (planning UI)',
        npm_args_install, shell=use_shell, cwd=str(ui_dir),
    )
    run_step(
        'npm run build (planning UI)',
        npm_args_build, shell=use_shell, cwd=str(ui_dir),
    )


def _run_tests() -> None:
    python_bin = str(venv_python_path())
    run_step(
        'unit tests',
        [python_bin, '-m', 'unittest', 'discover', '-s', 'tests'],
    )


def main() -> int:
    # ``--skip-tests`` opts out of the post-install sanity test run.
    # The tests use synthetic fixtures (e.g. ``PROJ-1``) and on a slow
    # machine they double the bootstrap time without telling you
    # anything about *your* configuration. The deps + UI bundle are
    # already installed before this point, so skipping is safe.
    skip_tests = '--skip-tests' in sys.argv[1:]
    _ensure_env_file()
    _ensure_venv()
    _install_python_deps()
    _maybe_build_ui_bundle()
    if not skip_tests:
        _run_tests()
    print(
        '\n'
        'Bootstrap complete.\n'
        '\n'
        'Next manual steps:\n'
        '  1. Fill the required secrets in .env\n'
        '  2. Run `make doctor` (POSIX) or '
        '`python -m kato_core_lib.validate_env --env-file .env --mode all` (any OS)\n'
        '  3. Run `make run` / `make compose-up` for local execution,\n'
        '     or `python scripts/run_local.py` directly on Windows.\n'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
