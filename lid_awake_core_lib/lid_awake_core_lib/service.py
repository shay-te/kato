"""Every few seconds: decide whether the computer should stay awake, and make it so.

The host supplies what only it knows, as plain callables: whether the operator
turned this on, and whether its agents are working. Everything that differs by
operating system arrives in one ``PlatformSupport``.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from utils_core_lib.utils_core_lib.tick_thread import TickThread

from lid_awake_core_lib.lid_awake_core_lib.decision import (
    KEEP_AWAKE_REASONS,
    LidAwakeInputs,
    LidAwakeReason,
    blocks_idle_sleep,
    decide,
)
from lid_awake_core_lib.lid_awake_core_lib.heartbeat import Heartbeat
from lid_awake_core_lib.lid_awake_core_lib.platform_support import PlatformSupport

# Well inside the watchdog's limit (``STALE_AFTER_MINUTES``), so a few slow
# ticks in a row never look like a crash.
TICK_SECONDS = 15.0
# Setup rarely changes and checking it takes three commands, so a good answer is
# reused for a while. A bad one is not: a finished setup should show at once.
GUARD_RECHECK_SECONDS = 300.0


class LidAwakeService(object):
    def __init__(
        self,
        *,
        is_enabled: Callable[[], bool],
        agents_working: Callable[[], bool],
        heartbeat: Heartbeat,
        support: PlatformSupport,
        clock: Callable[[], float] = time.monotonic,
        logger: logging.Logger | None = None,
    ) -> None:
        self._is_enabled = is_enabled
        self._agents_working = agents_working
        self._heartbeat = heartbeat
        self._support = support
        self._clock = clock
        self._logger = logger or logging.getLogger(__name__)
        self._guard_ready_until = 0.0
        self._status = _status(LidAwakeReason.OFF, '')
        self._status_lock = threading.Lock()
        self._ticker = TickThread(
            self.tick,
            interval_seconds=TICK_SECONDS,
            name='lid-awake',
            logger=self._logger,
        )

    def start(self) -> None:
        self._ticker.start()

    def stop(self) -> None:
        """Clean exit: hand sleep back now rather than leave it to the watchdog."""
        self._ticker.stop()
        self._release_idle_hold()
        error = self._release()
        if error:
            self._logger.warning('lid awake: could not restore sleep: %s', error)

    def status(self) -> dict:
        """``{reason, active, error}`` as of the last tick."""
        with self._status_lock:
            return dict(self._status)

    def tick(self) -> None:
        enabled = self._is_enabled()
        # A heartbeat left by a run that crashed is still this one's to clean
        # up, even with the setting off.
        if not enabled and not self._heartbeat.exists():
            self._release_idle_hold()
            self._publish(LidAwakeReason.OFF, '')
            return
        inputs = self._gather(enabled)
        reason = decide(inputs)
        keep_awake = reason in KEEP_AWAKE_REASONS
        # The lid hold does not stop idle sleep on every system: on Windows it
        # only changes what the lid does, so the idle timer still has to be held.
        if keep_awake or blocks_idle_sleep(inputs):
            self._support.idle_blocker.hold()
        else:
            self._release_idle_hold()
        error = self._hold() if keep_awake else self._release()
        self._publish(reason, error)

    def _gather(self, enabled: bool) -> LidAwakeInputs:
        supported = self._support.supported
        if not enabled or not supported:
            return LidAwakeInputs(enabled=enabled, supported=supported)
        return LidAwakeInputs(
            enabled=True,
            supported=True,
            guard_ready=self._is_guard_ready(),
            power=self._support.read_power(),
            heat_level=self._support.read_heat(),
            agents_working=self._read_agents_working(),
        )

    def _is_guard_ready(self) -> bool:
        if self._clock() < self._guard_ready_until:
            return True
        ready = self._support.guard_ready()
        self._guard_ready_until = (
            self._clock() + GUARD_RECHECK_SECONDS if ready else 0.0
        )
        return ready

    def _read_agents_working(self) -> bool:
        try:
            return bool(self._agents_working())
        except Exception:
            self._logger.exception('lid awake: could not read the agents')
            return False

    def _hold(self) -> str:
        # Heartbeat first: a crash right after the switch must not leave sleep
        # disabled with nothing for the watchdog to find.
        self._heartbeat.beat()
        switch = self._support.lid_switch
        if switch.is_held():
            return ''
        return switch.set_held(True)

    def _release(self) -> str:
        switch = self._support.lid_switch
        if switch is None or not self._heartbeat.exists():
            return ''
        if switch.is_held() is not False:
            error = switch.set_held(False)
            if error:
                # The heartbeat stays and goes stale, so the watchdog gets it.
                return error
        self._heartbeat.clear()
        return ''

    def _release_idle_hold(self) -> None:
        if self._support.idle_blocker is not None:
            self._support.idle_blocker.release()

    def _publish(self, reason: str, error: str) -> None:
        status = _status(reason, error)
        with self._status_lock:
            changed = status != self._status
            self._status = status
        if changed:
            self._logger.info('lid awake: %s %s', reason, error)


def _status(reason: str, error: str) -> dict:
    return {
        'reason': reason,
        'active': reason in KEEP_AWAKE_REASONS and not error,
        'error': error,
    }
