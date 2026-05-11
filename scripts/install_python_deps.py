"""Cross-platform replacement for ``scripts/install-python-deps.sh``.

Installs core-lib, email-core-lib, hydra-core, omegaconf, and finally the
local kato package. The single behavior difference from the .sh version is
that we look the path up via ``sys.executable`` style logic rather than
positional ``$1``, which makes the script work the same on Windows.

Usage:
    python scripts/install_python_deps.py [<python_executable>] [<mode>]

Where ``mode`` is ``standard`` (default) or ``editable``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _script_utils import REPO_ROOT, run_step  # noqa: E402


def main(argv: list[str]) -> int:
    python_bin = argv[1] if len(argv) > 1 else sys.executable
    install_mode = argv[2] if len(argv) > 2 else 'standard'

    run_step(
        f'pip install --upgrade pip',
        [python_bin, '-m', 'pip', 'install', '--upgrade', 'pip'],
    )
    run_step(
        'pip install core-lib + email-core-lib',
        [
            python_bin, '-m', 'pip', 'install', '--no-cache-dir',
            'core-lib>=0.2.0',
            'email-core-lib',
        ],
    )
    # core-lib 0.2.2 pins hydra-core==1.2; we run on Python 3.11 and need
    # the newer Hydra. --no-deps avoids fighting that pin.
    run_step(
        'pip install hydra-core + omegaconf (no-deps)',
        [
            python_bin, '-m', 'pip', 'install', '--no-cache-dir', '--no-deps',
            'hydra-core>=1.3.2',
            'omegaconf>=2.3.0',
        ],
    )
    webserver_path = REPO_ROOT / 'webserver'
    if install_mode == 'editable':
        run_step(
            'pip install -e . (editable)',
            [
                python_bin, '-m', 'pip', 'install',
                '--no-cache-dir', '--no-deps', '-e', str(REPO_ROOT),
            ],
        )
        run_step(
            'pip install -e webserver (editable)',
            [
                python_bin, '-m', 'pip', 'install',
                '--no-cache-dir', '-e', str(webserver_path),
            ],
        )
    else:
        run_step(
            'pip install . (standard)',
            [
                python_bin, '-m', 'pip', 'install',
                '--no-cache-dir', '--no-deps', str(REPO_ROOT),
            ],
        )
        run_step(
            'pip install webserver (standard)',
            [
                python_bin, '-m', 'pip', 'install',
                '--no-cache-dir', str(webserver_path),
            ],
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
