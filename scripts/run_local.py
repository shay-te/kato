"""Cross-platform replacement for ``scripts/run-local.sh``.

Loads ``~/.kato/settings.json`` (kato's ONLY config file — ``.env``
support was removed; the first-run wizard + Settings drawer replaced
it), hands the variables to the kato process via the parent
environment, and execs ``python -m kato_core_lib.main`` from the
project venv. Works on Windows, macOS, and Linux.

Usage:
    python scripts/run_local.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _script_utils import (  # noqa: E402
    REPO_ROOT,
    layered_env,
    read_kato_settings_file,
    venv_python_path,
)


def _bootstrap_if_needed(python_bin: Path) -> int:
    """First run on a fresh clone: bootstrap in place (venv + deps + UI
    bundle) so ``kato up`` is the ONLY command an operator ever needs —
    it then boots into the setup wizard in the browser. Tests are skipped
    here (a fresh clone hasn't been touched); ``kato bootstrap`` remains
    for an explicit full run."""
    if python_bin.exists():
        return 0
    print('first run detected (.venv missing) — bootstrapping kato…')
    rc = subprocess.call(
        [sys.executable, str(REPO_ROOT / 'scripts' / 'bootstrap.py'), '--skip-tests'],
        cwd=str(REPO_ROOT),
    )
    if rc != 0:
        print(
            'bootstrap failed — fix the errors above and re-run `kato up`.',
            file=sys.stderr,
        )
        return rc
    if not python_bin.exists():
        print(
            'bootstrap completed but .venv is still missing — run '
            '`python scripts/bootstrap.py` manually.',
            file=sys.stderr,
        )
        return 1
    return 0


# Exit-code contract with kato_core_lib.main._restart_in_place: kato exits
# with this code when it needs a clean relaunch (e.g. the agent backend was
# switched in the setup wizard). The launcher loop below respawns it — a
# full process teardown releases the webserver port on every platform,
# which an in-place exec cannot guarantee.
_RESTART_EXIT_CODE = 87


def main() -> int:
    python_bin = venv_python_path()
    rc = _bootstrap_if_needed(python_bin)
    if rc != 0:
        return rc

    # Precedence: real shell env > ~/.kato/settings.json (the Settings UI).
    # A missing/empty settings.json is fine — kato boots into SETUP MODE
    # and the first-run wizard collects everything from the browser.
    env = layered_env(os.environ, read_kato_settings_file())
    # Tell kato it is supervised: restarts go through a clean exit +
    # relaunch here instead of an in-place exec.
    env['KATO_SUPERVISED_RESTART'] = '1'

    while True:
        completed = subprocess.run(
            [str(python_bin), '-m', 'kato_core_lib.main'],
            cwd=REPO_ROOT,
            env=env,
        )
        if completed.returncode != _RESTART_EXIT_CODE:
            return completed.returncode
        # Reload settings saved through the UI before the relaunch so the
        # fresh process boots with them (shell still wins).
        env = layered_env(os.environ, read_kato_settings_file())
        env['KATO_SUPERVISED_RESTART'] = '1'
        print('kato requested a restart — relaunching…')


if __name__ == '__main__':
    raise SystemExit(main())
