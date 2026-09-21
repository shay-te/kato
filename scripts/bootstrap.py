"""Cross-platform replacement for ``scripts/bootstrap.sh``.

Creates ``.venv``, installs kato + the webserver in editable mode, builds
the React planning UI bundle when ``npm`` is available, and runs the test
suite. Works on Windows, macOS, and Linux.

Usage:
    python scripts/bootstrap.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _script_utils import (  # noqa: E402
    REPO_ROOT,
    VENV_DIR,
    have_executable,
    require_supported_python,
    run_step,
    venv_python_path,
)


def _venv_has_pip(python_bin: Path) -> bool:
    """Is this venv actually usable, or just an interpreter with no pip?

    ``python -m venv`` creates ``bin/python`` BEFORE it bootstraps pip, so a
    run that dies at the pip step still leaves an interpreter behind. The
    common cause is Debian/Ubuntu, which strips ``ensurepip`` out of the base
    Python into a separate ``pythonX.Y-venv`` package.
    """
    if not python_bin.exists():
        return False
    try:
        probe = subprocess.run(
            [str(python_bin), '-c', 'import pip'],
            capture_output=True,
        )
    except OSError:
        return False
    return probe.returncode == 0


def _venv_repair_hint() -> str:
    """Name the package AND the leftover directory.

    Both halves are needed: installing the package alone does not help while
    the half-built venv is still on disk, because the existence check below
    skips creation and the failure resurfaces later as ``No module named
    pip`` — which mentions neither venv nor the package.
    """
    version = f'{sys.version_info.major}.{sys.version_info.minor}'
    return (
        f'\n{VENV_DIR} exists but has no pip, so it cannot be used.\n'
        f'On Debian/Ubuntu that means the matching venv package is missing:\n'
        f'\n'
        f'    sudo apt install python{version}-venv\n'
        f'    rm -rf {VENV_DIR}\n'
        f'\n'
        f'then re-run this script. Install the VERSION-MATCHED package — a\n'
        f'bare "python3-venv" can target a different interpreter than the\n'
        f'python{version} running this script.\n'
    )


def _ensure_venv() -> None:
    python_bin = venv_python_path()
    if _venv_has_pip(python_bin):
        return
    if python_bin.exists():
        # A leftover from an earlier failed run. Say so instead of pressing on
        # into a pip command that cannot work.
        print(_venv_repair_hint(), file=sys.stderr)
        sys.exit(1)
    run_step('python -m venv .venv', [sys.executable, '-m', 'venv', str(VENV_DIR)])
    # ``venv`` can exit 0 and still leave no pip (``--without-pip``, or a
    # distro that half-provides ensurepip), so verify rather than assume.
    if not _venv_has_pip(python_bin):
        print(_venv_repair_hint(), file=sys.stderr)
        sys.exit(1)


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


def _node_can_build() -> tuple[bool, str]:
    """Can this Node actually run the bundler?

    A CAPABILITY probe, not version arithmetic: Vite reaches for the global
    ``crypto.getRandomValues``, which older Node does not expose, and the
    exact release that boundary falls on has moved around. Asking Node
    whether it has the thing cannot be wrong about the boundary.

    Returns ``(ok, reason)`` — ``reason`` is operator-facing when not ok.
    """
    try:
        version = subprocess.run(
            ['node', '--version'], capture_output=True, text=True,
        )
    except OSError as exc:
        return False, f'node is not runnable ({exc})'
    running = (version.stdout or version.stderr or '').strip() or 'unknown'
    probe = subprocess.run(
        [
            'node', '-e',
            'if (!globalThis.crypto || !globalThis.crypto.getRandomValues)'
            ' process.exit(1)',
        ],
        capture_output=True,
    )
    if probe.returncode != 0:
        return False, (
            f'node {running} has no global crypto.getRandomValues, which the\n'
            f'    bundler needs (this is the "crypto$2.getRandomValues is not a\n'
            f'    function" error). Install Node 20 LTS or newer.'
        )
    return True, running


def _maybe_build_ui_bundle() -> None:
    """Best effort. The bundle is COMMITTED, so this step is an optimization.

    It used to be fatal whenever npm merely EXISTED: npm absent skipped
    politely and used the committed bundle, while npm present-but-unusable
    (old Node) killed the whole bootstrap. Backwards — the harder failure
    got the harsher treatment, and an operator who only wanted to RUN kato
    was blocked by a build they did not need.

    A developer changing ``webserver/ui/src`` still runs ``npm run build``
    themselves and sees any real error there.
    """
    if not have_executable('npm'):
        print('==> skipping webserver/ui build (npm not found; using committed bundle)')
        return
    node_ok, reason = _node_can_build()
    if not node_ok:
        print(
            f'==> skipping webserver/ui build (using committed bundle)\n'
            f'    {reason}',
            flush=True,
        )
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
    for label, args in (
        ('npm install (planning UI)', npm_args_install),
        ('npm run build (planning UI)', npm_args_build),
    ):
        print(f'==> {label}', flush=True)
        completed = subprocess.run(
            args, shell=use_shell, cwd=str(ui_dir),
        )
        if completed.returncode != 0:
            # Warn, do NOT exit: the committed bundle is still on disk and
            # kato serves it. Blocking here would turn a UI-toolchain
            # problem into "kato will not install".
            print(
                f'\n==> WARNING: {label} failed (exit {completed.returncode}).\n'
                f'    Continuing with the committed bundle — kato will run.\n'
                f'    Rebuild later with: cd webserver/ui && npm install'
                f' && npm run build\n',
                file=sys.stderr,
                flush=True,
            )
            return


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
    # FIRST, before anything is created: an interpreter below the minimum
    # fails several steps later wearing someone else's error message.
    require_supported_python()
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
        '  1. Run `kato up` — the first-run wizard in the browser\n'
        '     collects everything (config lives in ~/.kato/settings.json)\n'
        '  2. Run `kato doctor` any time to validate the environment\n'
        '     (`kato` is on PATH after `pip install -e .`)\n'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
