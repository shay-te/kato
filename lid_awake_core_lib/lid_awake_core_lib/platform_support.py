"""What each platform has to supply, so the service never asks which one it is on."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from lid_awake_core_lib.lid_awake_core_lib.decision import PowerState


class LidSwitch(Protocol):
    """The hold that survives a closed lid. Every change is read back."""

    def is_held(self) -> bool | None:
        """``None`` when the system could not be asked."""

    def set_held(self, held: bool) -> str:
        """'' when the system confirms it; otherwise why not."""


class IdleBlocker(Protocol):
    """The milder hold: stops idle sleep, and ends with the host by itself."""

    holding: bool

    def hold(self) -> None: ...

    def release(self) -> None: ...


def _no_guard() -> int:
    return 0


@dataclass(frozen=True)
class PlatformSupport:
    """``lid_switch`` is ``None`` on a system the lib has nothing for."""

    lid_switch: LidSwitch | None = None
    idle_blocker: IdleBlocker | None = None
    read_power: Callable[[], PowerState] = PowerState
    read_heat: Callable[[], int | None] = lambda: None
    guard_ready: Callable[[], bool] = lambda: False
    install_guard: Callable[[], int] = _no_guard
    remove_guard: Callable[[], int] = _no_guard

    @property
    def supported(self) -> bool:
        return self.lid_switch is not None


UNSUPPORTED = PlatformSupport()
