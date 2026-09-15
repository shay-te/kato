"""Build ``kato.exe`` from ``kato.py`` (Windows-only convenience).

Run this once after ``python scripts/bootstrap.py`` on Windows:

    .\\.venv\\Scripts\\python.exe tools\\kato\\build.py

Output: ``kato.exe`` at the repo root. Self-contained (~8 MB),
bundles its own Python interpreter — no venv needed at runtime.
After building, the operator types ``.\\kato.exe <target>`` (or
just ``kato <target>`` if the repo root is on ``PATH``).

Safe to run while kato is running: see ``_install_binary``.

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
# Where PyInstaller writes the binary. NOT the repo root — see _run_pyinstaller.
DIST_DIR = WORK_DIR / 'dist'


def _binary_name() -> str:
    return 'kato.exe' if os.name == 'nt' else 'kato'


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
    """Build the binary with --onefile into the work directory.

    Never straight into the repo root. PyInstaller deletes the existing binary
    before writing the new one, and Windows refuses to delete an ``.exe`` that
    is running — so rebuilding while kato was up failed with
    ``PermissionError: [WinError 5] Access is denied: '...\\kato.exe'``.
    ``_install_binary`` puts the result in place instead.
    """
    print(f'==> building from {KATO_PY.name} ...', flush=True)
    subprocess.check_call([
        str(python), '-m', 'PyInstaller',
        '--onefile',
        '--name', 'kato',
        '--distpath', str(DIST_DIR),
        '--workpath', str(WORK_DIR),
        '--specpath', str(WORK_DIR),
        '--clean',
        str(KATO_PY),
    ])


def _moved_aside_name(target: Path) -> Path:
    """A free ``<name>.old`` / ``<name>.old1`` / … next to ``target``."""
    candidate = target.with_name(f'{target.name}.old')
    index = 1
    while candidate.exists():
        candidate = target.with_name(f'{target.name}.old{index}')
        index += 1
    return candidate


def _install_binary(
    built: Path, target: Path, *, replace=os.replace, remove=os.remove,
) -> Path | None:
    """Put ``built`` at ``target``. Returns where the previous binary was moved
    aside to, or ``None`` when it could simply be replaced.

    Windows will not delete or overwrite a running ``.exe``, but it WILL rename
    one. So when the replace is refused, the old binary is renamed out of the
    way — the kato running from it carries on undisturbed — and the new one
    takes its name; the next start runs the new build. Copies moved aside by
    earlier builds are removed first, unless something still runs from them.

    Raises ``PermissionError`` when even the rename is refused (the file is held
    by something that forbids that too).
    """
    for stale in sorted(target.parent.glob(f'{target.name}.old*')):
        try:
            remove(stale)
        except OSError:
            pass  # still in use; the next build tries again
    try:
        replace(built, target)
        return None
    except PermissionError:
        pass
    moved_aside = _moved_aside_name(target)
    replace(target, moved_aside)
    replace(built, target)
    return moved_aside


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
    built = DIST_DIR / _binary_name()
    output = REPO_ROOT / _binary_name()
    try:
        if not built.is_file():
            print('error: build finished but output binary missing', file=sys.stderr)
            return 1
        moved_aside = _install_binary(built, output)
    except PermissionError as exc:
        print(
            f'error: could not put the new binary at {output}: {exc}\n'
            f'       something is holding the old one. Close every running kato '
            f'and build again.',
            file=sys.stderr,
        )
        return 1
    finally:
        _cleanup_build_artifacts()
    print(f'==> done. {output.name} is at the repo root.')
    if moved_aside is not None:
        print(
            f'    kato was running from the old binary, so it was moved aside to '
            f'{moved_aside.name} (the next build deletes it). Restart kato to run '
            f'the new build.'
        )
    if os.name == 'nt':
        print('    use as: .\\kato.exe <target>')
        print('    or put the repo root on PATH and just type: kato <target>')
    else:
        print('    use as: ./kato <target>')
    return 0


if __name__ == '__main__':
    sys.exit(main())
