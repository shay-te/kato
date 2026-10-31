"""What restores the lid action on Windows when the host cannot.

The same small PowerShell script does both jobs:
- the host starts it, hidden and detached, the moment it holds the lid, so it
  outlives a crash and restores the setting once the heartbeat goes stale
- a Run value starts it at every logon, which covers a reboot or a logoff with
  the lid action still changed

Nothing here needs elevation, and neither does the script: it is the operator's
own setting. A scheduled task every minute was rejected because it flashes a
console window each time.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from lid_awake_core_lib.lid_awake_core_lib.commands import NO_WINDOW, run_command
from lid_awake_core_lib.lid_awake_core_lib.mac_crash_guard import STALE_AFTER_MINUTES
from lid_awake_core_lib.lid_awake_core_lib.windows_lid_switch import (
    LID_ACTION,
    POWERCFG,
)

RUN_KEY = r'HKCU\Software\Microsoft\Windows\CurrentVersion\Run'
POWERSHELL = 'powershell.exe'
CHECK_EVERY_SECONDS = 30

_BREAKAWAY_FROM_JOB = getattr(subprocess, 'CREATE_BREAKAWAY_FROM_JOB', 0)
_NEW_PROCESS_GROUP = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)


@dataclass(frozen=True)
class WindowsGuardIdentity:
    run_value: str
    script: Path
    saved: Path


def _quoted(path: Path | str) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def render_watchdog(heartbeat: Path | str, saved: Path | str) -> str:
    """The script. It only ever puts the operator's own values back."""
    lid_action = ' '.join(LID_ACTION)
    return f"""# Restores the lid action when the host stops proving it is alive.
$heartbeat = {_quoted(heartbeat)}
$saved = {_quoted(saved)}
while (Test-Path -LiteralPath $heartbeat) {{
    $age = (Get-Date) - (Get-Item -LiteralPath $heartbeat).LastWriteTime
    if ($age.TotalMinutes -ge {STALE_AFTER_MINUTES}) {{
        if (Test-Path -LiteralPath $saved) {{
            $lid = Get-Content -LiteralPath $saved -Raw | ConvertFrom-Json
            & {POWERCFG} /setacvalueindex {lid_action} $lid.ac
            & {POWERCFG} /setdcvalueindex {lid_action} $lid.dc
            & {POWERCFG} /setactive SCHEME_CURRENT
            Remove-Item -LiteralPath $saved -Force
        }}
        Remove-Item -LiteralPath $heartbeat -Force
        exit 0
    }}
    Start-Sleep -Seconds {CHECK_EVERY_SECONDS}
}}
"""


def watchdog_command(script: Path | str) -> list[str]:
    return [
        POWERSHELL, '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
        '-ExecutionPolicy', 'Bypass', '-File', str(script),
    ]


def start_watchdog(script: Path | str, *, spawn=subprocess.Popen):
    """Start it detached. Some job objects refuse a breakaway, so fall back."""
    flags = NO_WINDOW | _NEW_PROCESS_GROUP
    for creationflags in (flags | _BREAKAWAY_FROM_JOB, flags):
        try:
            return spawn(
                watchdog_command(script),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except OSError:
            continue
    return None


def guard_is_ready(identity: WindowsGuardIdentity) -> bool:
    if not identity.script.exists():
        return False
    finished = run_command(['reg', 'query', RUN_KEY, '/v', identity.run_value])
    return finished is not None and finished.returncode == 0


def install_guard(identity: WindowsGuardIdentity, heartbeat: Path | str) -> int:
    identity.script.parent.mkdir(parents=True, exist_ok=True)
    identity.script.write_text(
        render_watchdog(heartbeat, identity.saved), encoding='utf-8',
    )
    command = subprocess.list2cmdline(watchdog_command(identity.script))
    finished = run_command([
        'reg', 'add', RUN_KEY, '/v', identity.run_value,
        '/t', 'REG_SZ', '/d', command, '/f',
    ])
    return 1 if finished is None else finished.returncode


def remove_guard(identity: WindowsGuardIdentity, switch) -> int:
    """Put the lid action back first: the guard is what would have done it."""
    error = switch.set_held(False)
    if error:
        return 1
    run_command(['reg', 'delete', RUN_KEY, '/v', identity.run_value, '/f'])
    identity.script.unlink(missing_ok=True)
    return 0
