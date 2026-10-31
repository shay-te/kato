"""The root watchdog that restores sleep when the host cannot, and its setup.

The host turns the switch back off on a clean exit, but a crash, a hang or a
logout skips that, and a Mac left with sleep disabled stays on in a bag until it
is flat or hot. So a small root job checks the heartbeat every 30 seconds and
restores sleep by itself. It runs as a LaunchDaemon, not a LaunchAgent, because
an agent dies with the login session and logout is one of the cases it covers.

What setup installs, and all that root ever gets:
- a sudoers rule for exactly the two commands the host needs, with literal
  arguments, so the grant cannot be widened by adding arguments
- the watchdog script, owned by root so that nothing the host runs can edit it
- the LaunchDaemon that runs it

The operator runs this once in a terminal and types their password into
``sudo``. The host never sees it, and an agent cannot do this step.
"""

from __future__ import annotations

import getpass
import plistlib
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from lid_awake_core_lib.lid_awake_core_lib.commands import run_command
from lid_awake_core_lib.lid_awake_core_lib.mac_power import PMSET
from lid_awake_core_lib.lid_awake_core_lib.mac_sleep_switch import (
    SUDO,
    disable_sleep_command,
)

LAUNCHCTL = '/bin/launchctl'

CHECK_EVERY_SECONDS = 30
# Must comfortably outlast a slow tick: this going off puts a closed Mac to
# sleep under healthy work.
STALE_AFTER_MINUTES = 2

_SAFE_USER = re.compile(r'^[A-Za-z0-9._-]+$')


@dataclass(frozen=True)
class GuardIdentity:
    """Whose guard this is: the host's names on the system's shared folders."""

    label: str
    directory: str
    sudoers_name: str

    @property
    def script_path(self) -> str:
        return f'{self.directory}/lid-awake-watchdog.sh'

    @property
    def plist_path(self) -> str:
        return f'/Library/LaunchDaemons/{self.label}.plist'

    @property
    def sudoers_path(self) -> str:
        return f'/etc/sudoers.d/{self.sudoers_name}'


def render_sudoers(user: str) -> str:
    if not _SAFE_USER.match(user):
        raise ValueError(f'cannot write a sudoers rule for the user {user!r}')
    commands = ', '.join(
        ' '.join(disable_sleep_command(disabled)) for disabled in (True, False)
    )
    return f'{user} ALL=(root) NOPASSWD: {commands}\n'


def render_watchdog(heartbeat: Path | str, *, pmset: str = PMSET) -> str:
    """The script root runs. It only ever turns sleep back ON.

    It reads nothing from the heartbeat but its age, so a user-writable file
    cannot make root do anything else.
    """
    return f"""#!/bin/sh
# Restores normal sleep when the host stops proving it is alive.
heartbeat={shlex.quote(str(heartbeat))}
[ -e "$heartbeat" ] || exit 0
{shlex.quote(pmset)} -g | /usr/bin/grep -Eq 'SleepDisabled[[:space:]]+1' || exit 0
[ -n "$(/usr/bin/find "$heartbeat" -mmin +{STALE_AFTER_MINUTES})" ] || exit 0
exec {shlex.quote(pmset)} -a disablesleep 0
"""


def render_plist(identity: GuardIdentity) -> bytes:
    return plistlib.dumps({
        'Label': identity.label,
        'ProgramArguments': ['/bin/sh', identity.script_path],
        'RunAtLoad': True,
        'StartInterval': CHECK_EVERY_SECONDS,
    })


def render_install_script(identity: GuardIdentity, source: Path) -> str:
    sudoers = shlex.quote(str(source / 'sudoers'))
    watchdog = shlex.quote(str(source / 'watchdog.sh'))
    plist = shlex.quote(str(source / 'watchdog.plist'))
    directory = shlex.quote(identity.directory)
    script_path = shlex.quote(identity.script_path)
    plist_path = shlex.quote(identity.plist_path)
    sudoers_path = shlex.quote(identity.sudoers_path)
    # The sudo rule goes in LAST: if anything before it fails, the host cannot
    # hold sleep off at all, so no state exists where it can with no watchdog.
    return f"""#!/bin/sh
set -eu
/usr/sbin/visudo -cf {sudoers}
/bin/mkdir -p {directory}
/usr/sbin/chown root:wheel {directory}
/bin/chmod 755 {directory}
/usr/bin/install -o root -g wheel -m 0755 {watchdog} {script_path}
/usr/bin/install -o root -g wheel -m 0644 {plist} {plist_path}
{LAUNCHCTL} bootout system/{identity.label} 2>/dev/null || true
{LAUNCHCTL} bootstrap system {plist_path}
/usr/bin/install -o root -g wheel -m 0440 {sudoers} {sudoers_path}
"""


def render_remove_script(identity: GuardIdentity) -> str:
    directory = shlex.quote(identity.directory)
    script_path = shlex.quote(identity.script_path)
    plist_path = shlex.quote(identity.plist_path)
    sudoers_path = shlex.quote(identity.sudoers_path)
    return f"""#!/bin/sh
{PMSET} -a disablesleep 0
/bin/rm -f {sudoers_path}
{LAUNCHCTL} bootout system/{identity.label} 2>/dev/null || true
/bin/rm -f {plist_path} {script_path}
/bin/rmdir {directory} 2>/dev/null || true
exit 0
"""


def _watchdog_is_loaded(identity: GuardIdentity) -> bool:
    finished = run_command([LAUNCHCTL, 'print', f'system/{identity.label}'])
    return finished is not None and finished.returncode == 0


def _sudo_rule_allows(disabled: bool) -> bool:
    # ``-l <command>`` asks without running it.
    finished = run_command([SUDO, '-n', '-l', *disable_sleep_command(disabled)])
    return finished is not None and finished.returncode == 0


def guard_is_ready(identity: GuardIdentity) -> bool:
    """True only when the watchdog runs AND the host may flip the switch both ways.

    Both directions matter: a rule that could only turn sleep off is the exact
    trap this file exists to prevent.
    """
    return (
        _watchdog_is_loaded(identity)
        and _sudo_rule_allows(True)
        and _sudo_rule_allows(False)
    )


def _run_as_root(script: str, folder: Path) -> int:
    path = folder / 'run.sh'
    path.write_text(script, encoding='utf-8')
    # Stdin stays the operator's terminal: this is the one place sudo may ask.
    return subprocess.call([SUDO, '/bin/sh', str(path)])


def install_guard(
    identity: GuardIdentity,
    heartbeat: Path | str,
    *,
    user: str | None = None,
) -> int:
    """Install or refresh the guard. Asks for the password; exit code of sudo."""
    with tempfile.TemporaryDirectory(prefix='lid-awake-') as folder:
        source = Path(folder)
        (source / 'sudoers').write_text(
            render_sudoers(user or getpass.getuser()), encoding='utf-8',
        )
        (source / 'watchdog.sh').write_text(
            render_watchdog(heartbeat), encoding='utf-8',
        )
        (source / 'watchdog.plist').write_bytes(render_plist(identity))
        return _run_as_root(render_install_script(identity, source), source)


def remove_guard(identity: GuardIdentity) -> int:
    with tempfile.TemporaryDirectory(prefix='lid-awake-') as folder:
        return _run_as_root(render_remove_script(identity), Path(folder))
