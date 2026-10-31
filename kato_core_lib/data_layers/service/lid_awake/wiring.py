"""Build the lib's service from kato's settings, sessions and names."""

from __future__ import annotations

from lid_awake_core_lib.lid_awake_core_lib.heartbeat import Heartbeat
from lid_awake_core_lib.lid_awake_core_lib.platform_support import PlatformSupport
from lid_awake_core_lib.lid_awake_core_lib.service import LidAwakeService

from kato_core_lib.data_layers.service.lid_awake.agents import agents_are_working
from kato_core_lib.data_layers.service.lid_awake.identity import (
    heartbeat_path,
    lid_awake_support,
)
from kato_core_lib.data_layers.service.lid_awake.settings import lid_awake_enabled


def build_and_start_lid_awake_service(
    *,
    session_manager,
    support: PlatformSupport | None = None,
    autostart: bool = True,
) -> LidAwakeService:
    service = LidAwakeService(
        is_enabled=lid_awake_enabled,
        agents_working=lambda: agents_are_working(session_manager),
        heartbeat=Heartbeat(heartbeat_path()),
        support=support or lid_awake_support(),
    )
    if autostart:
        service.start()
    return service
