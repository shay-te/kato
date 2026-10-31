"""The one switch that survives a closed lid: ``pmset disablesleep``.

Every change is read back, so the host reports what the Mac is really doing
and never what it asked for. The two ``sudo`` commands below must match the sudoers
rule in ``mac_crash_guard`` character for character.
"""

from __future__ import annotations

import re

from lid_awake_core_lib.lid_awake_core_lib.commands import run_command
from lid_awake_core_lib.lid_awake_core_lib.mac_power import PMSET

SUDO = '/usr/bin/sudo'

_SLEEP_DISABLED = re.compile(r'^\s*SleepDisabled\s+(\d+)', re.MULTILINE)


def disable_sleep_command(disabled: bool) -> list[str]:
    return [PMSET, '-a', 'disablesleep', '1' if disabled else '0']


class MacSleepSwitch(object):
    def is_held(self) -> bool | None:
        """Whether sleep is held off; ``None`` when the Mac could not be asked.

        macOS leaves the line out of ``pmset -g`` while sleep is on.
        """
        finished = run_command([PMSET, '-g'])
        if finished is None or finished.returncode != 0:
            return None
        match = _SLEEP_DISABLED.search(finished.stdout)
        return bool(match) and match.group(1) != '0'

    def set_held(self, held: bool) -> str:
        """Change the switch. '' when the Mac confirms it; otherwise why not."""
        finished = run_command([SUDO, '-n', *disable_sleep_command(held)])
        if finished is None:
            return 'pmset did not answer'
        if finished.returncode != 0:
            detail = finished.stderr.strip() or f'exit {finished.returncode}'
            return f'pmset failed: {detail}'
        if self.is_held() is not held:
            return 'pmset ran, but the Mac did not change'
        return ''
