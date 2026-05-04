"""Build ``kato.exe`` from ``kato.py`` (Windows-only convenience).

Run this once after ``python scripts/bootstrap.py`` on Windows:

    .\\.venv\\Scripts\\python.exe tools\\kato\\build.py

Output: ``kato.exe`` at the repo root. Self-contained (~8 MB),
bundles its own Python interpreter — no venv needed at runtime.
After building, the operator types ``.\\kato.exe <target>`` (or
just ``kato <target>`` if the repo root is on ``PATH``).

POSIX hosts don't need this — the committed ``kato`` shell script
at the repo root does the same dispatch with no build step.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent.parent
KATO_PY = THIS_DIR / 'kato.py'
WORK_DIR = REPO_ROOT / 'build' / 'make-exe'


def _venv_python() -> Path:
    if os.name == 'nt':
        return REPO_ROOT / '.venv' / 'Scripts' / 'python.exe'
    return REPO_ROOT / '.venv' / 'bin' / 'python'


def _ensure_pyinstaller(python: Path) -> None:
    """Install PyInstaller into the venv if it isn't already there."""
    probe = subprocess.run(
        [str(python), '-c', 'import PyInstaller'],
        capture_output=True,
    )
    if probe.returncode == 0:
        return
    print('==> installing PyInstaller into venv...', flush=True)
    subprocess.check_call(
        [str(python), '-m', 'pip', 'install', '--no-cache-dir', 'pyinstaller'],
    )


def _run_pyinstaller(python: Path) -> None:
    """Build the binary with --onefile.

    Output is named ``make`` (PyInstaller adds ``.exe`` on Windows).
    """
    print(f'==> building from {KATO_PY.name} ...', flush=True)
    subprocess.check_call([
        str(python), '-m', 'PyInstaller',
        '--onefile',
        '--name', 'kato',
        '--distpath', str(REPO_ROOT),
        '--workpath', str(WORK_DIR),
        '--specpath', str(WORK_DIR),
        '--clean',
        str(KATO_PY),
    ])


def _cleanup_build_artifacts() -> None:
    """Wipe the temporary build directory; keep only the final binary."""
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR, ignore_errors=True)
    parent = REPO_ROOT / 'build'
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()


def main() -> int:
    if not KATO_PY.is_file():
        print(f'error: {KATO_PY} not found', file=sys.stderr)
        return 1
    python = _venv_python()
    if not python.is_file():
        print(
            f'error: venv python not found at {python}\n'
            f'       run ``python scripts/bootstrap.py`` first.',
            file=sys.stderr,
        )
        return 1
    _ensure_pyinstaller(python)
    _run_pyinstaller(python)
    _cleanup_build_artifacts()
    output = REPO_ROOT / ('kato.exe' if os.name == 'nt' else 'kato')
    if output.is_file():
        print(f'==> done. {output.name} is at the repo root.')
        if os.name == 'nt':
            print('    use as: .\\kato.exe <target>')
            print('    or put the repo root on PATH and just type: kato <target>')
        else:
            print('    use as: ./kato <target>')
        return 0
    print('error: build finished but output binary missing', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
