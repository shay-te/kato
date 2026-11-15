"""Is ``git`` really installed — and if not, how does this computer get it?

A ``git`` on PATH is not proof: a Mac without Apple's command line tools has
``/usr/bin/git``, a stand-in that only offers to install them (and pops that
offer up each time it runs). So on a Mac the tools are asked about first
(``xcode-select -p``), and the stand-in is never run. Elsewhere ``git --version``
must answer.

On a Mac kato can start Apple's own installer (``xcode-select --install``: a
system dialog the operator confirms); on Linux and Windows it shows the command.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable

CHECK_TIMEOUT_SECONDS = 10

INSTALL = {
    'darwin': {
        'command': 'xcode-select --install',
        'text': 'Apple’s command line tools include git. Kato can open their installer.',
        'can_start': True,
    },
    'win32': {
        'command': 'winget install --id Git.Git -e',
        'text': 'Or download it from git-scm.com/downloads/win.',
        'can_start': False,
    },
    'linux': {
        'command': 'sudo apt install git',
        'text': 'Or your distribution’s package manager (dnf, pacman, apk …).',
        'can_start': False,
    },
}


def git_status(
    *,
    platform: str = sys.platform,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict:
    """``{installed, version, install}`` — ``install`` only when it is missing."""
    path = which('git')
    if path and platform == 'darwin' and not _mac_tools_installed(run):
        path = None
    version = _version(path, run) if path else ''
    if version:
        return {'installed': True, 'version': version, 'install': None}
    return {'installed': False, 'version': '', 'install': install_hint(platform)}


def install_hint(platform: str) -> dict:
    key = 'darwin' if platform == 'darwin' else 'win32' if platform == 'win32' else 'linux'
    return dict(INSTALL[key])


def start_mac_install(
    *,
    platform: str = sys.platform,
    spawn: Callable[..., object] = subprocess.Popen,
) -> bool:
    """Open Apple's command line tools installer. Mac only; never waits on it."""
    if platform != 'darwin':
        return False
    try:
        spawn(['xcode-select', '--install'], stdin=subprocess.DEVNULL,
              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return True


def _mac_tools_installed(run) -> bool:
    try:
        result = run(['xcode-select', '-p'], capture_output=True, text=True,
                     timeout=CHECK_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _version(path: str, run) -> str:
    try:
        result = run([path, '--version'], capture_output=True, text=True,
                     timeout=CHECK_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ''
    output = (result.stdout or '').strip()
    if result.returncode != 0 or not output.startswith('git version'):
        return ''
    return output.removeprefix('git version').strip()
