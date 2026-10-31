"""The Windows switch that survives a closed lid: the power plan's lid action.

``powercfg`` sets what closing the lid does; "do nothing" is the only way to
keep a closed Windows laptop running. It is the operator's own setting, so the
value it had is kept in a file until it is put back. Every change is read back,
which also catches a power plan locked by Group Policy.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json

from lid_awake_core_lib.lid_awake_core_lib.commands import run_command

POWERCFG = 'powercfg'
LID_ACTION = ['SCHEME_CURRENT', 'SUB_BUTTONS', 'LIDACTION']
DO_NOTHING = 0

# The labels around these values are translated; the hex values are not. The
# query prints the AC value and then the DC one, and nothing else in ``0x`` form.
_HEX_VALUE = re.compile(r'0x([0-9a-fA-F]+)')


def parse_lid_actions(output: str) -> tuple[int, int] | None:
    values = _HEX_VALUE.findall(output)
    if len(values) < 2:
        return None
    return int(values[-2], 16), int(values[-1], 16)


class WindowsLidSwitch(object):
    def __init__(self, *, saved: Path, start_watchdog: Callable[[], object]) -> None:
        self._saved = Path(saved)
        self._start_watchdog = start_watchdog

    def is_held(self) -> bool | None:
        """Held means the host's own "do nothing": a saved value proves it."""
        actions = self._read()
        if actions is None:
            return None
        return actions == (DO_NOTHING, DO_NOTHING) and self._saved.exists()

    def set_held(self, held: bool) -> str:
        return self._hold() if held else self._restore()

    def _hold(self) -> str:
        actions = self._read()
        if actions is None:
            return 'powercfg did not answer'
        if not self._saved.exists():
            atomic_write_json(
                self._saved, {'ac': actions[0], 'dc': actions[1]}, label='lid action',
            )
        error = self._apply(DO_NOTHING, DO_NOTHING)
        if not error:
            self._start_watchdog()
        return error

    def _restore(self) -> str:
        original = self._read_saved()
        if original is None:
            return ''
        error = self._apply(*original)
        if not error:
            self._saved.unlink(missing_ok=True)
        return error

    def _read(self) -> tuple[int, int] | None:
        finished = run_command([POWERCFG, '/query', *LID_ACTION])
        if finished is None or finished.returncode != 0:
            return None
        return parse_lid_actions(finished.stdout)

    def _read_saved(self) -> tuple[int, int] | None:
        try:
            saved = json.loads(self._saved.read_text(encoding='utf-8'))
            return int(saved['ac']), int(saved['dc'])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _apply(self, ac: int, dc: int) -> str:
        for command in (
            [POWERCFG, '/setacvalueindex', *LID_ACTION, str(ac)],
            [POWERCFG, '/setdcvalueindex', *LID_ACTION, str(dc)],
            [POWERCFG, '/setactive', 'SCHEME_CURRENT'],
        ):
            finished = run_command(command)
            if finished is None:
                return 'powercfg did not answer'
            if finished.returncode != 0:
                detail = finished.stderr.strip() or finished.stdout.strip()
                return f'powercfg failed: {detail or finished.returncode}'
        if self._read() != (ac, dc):
            return 'powercfg ran, but the computer did not change'
        return ''
