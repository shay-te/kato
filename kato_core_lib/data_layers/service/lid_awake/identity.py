"""Kato's names in the system places the lid-closed feature writes to."""

from __future__ import annotations

from pathlib import Path

from lid_awake_core_lib.lid_awake_core_lib.mac_crash_guard import GuardIdentity
from lid_awake_core_lib.lid_awake_core_lib.platform_support import PlatformSupport
from lid_awake_core_lib.lid_awake_core_lib.platforms import (
    HostIdentity,
    platform_support,
)
from lid_awake_core_lib.lid_awake_core_lib.windows_crash_guard import WindowsGuardIdentity

from kato_core_lib.helpers.kato_paths_utils import kato_home_path

HEARTBEAT_PATH_ENV = 'KATO_LID_AWAKE_HEARTBEAT_PATH'

GUARD_IDENTITY = GuardIdentity(
    label='com.kato.lid-awake-watchdog',
    directory='/Library/Application Support/Kato',
    sudoers_name='kato-lid-awake',
)


def heartbeat_path() -> Path:
    return kato_home_path('lid-awake.heartbeat', env_key=HEARTBEAT_PATH_ENV)


def host_identity() -> HostIdentity:
    heartbeat = heartbeat_path()
    return HostIdentity(
        who='kato',
        why='kato is keeping agents running with the lid closed',
        heartbeat=heartbeat,
        mac=GUARD_IDENTITY,
        windows=WindowsGuardIdentity(
            run_value='KatoLidAwakeWatchdog',
            script=heartbeat.with_name('lid-awake-watchdog.ps1'),
            saved=heartbeat.with_name('lid-awake-lid-action.json'),
        ),
    )


def lid_awake_support() -> PlatformSupport:
    return platform_support(host_identity())
