"""Stop Windows idling to sleep: ``SetThreadExecutionState``, on a thread of its own.

Windows ties the request to the calling thread, so it gets a thread that stays
alive for exactly as long as the hold. It does nothing about a closed lid:
Microsoft's documentation is explicit that it cannot, and that is what
``windows_lid_switch`` is for.
"""

from __future__ import annotations

import ctypes
import threading

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
JOIN_TIMEOUT_SECONDS = 2.0


class WindowsIdleSleepBlocker(object):
    def __init__(self, *, kernel32=None) -> None:
        self._kernel32 = kernel32
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def holding(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def hold(self) -> None:
        if self.holding:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._hold_until_stopped,
            args=(self._stop,),
            name='lid-awake-idle-blocker',
            daemon=True,
        )
        self._thread.start()

    def release(self) -> None:
        thread, self._thread = self._thread, None
        if thread is None:
            return
        self._stop.set()
        thread.join(JOIN_TIMEOUT_SECONDS)

    def _hold_until_stopped(self, stop: threading.Event) -> None:
        kernel32 = self._kernel32 or ctypes.windll.kernel32
        kernel32.SetThreadExecutionState.restype = ctypes.c_uint32
        kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
        kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        stop.wait()
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)
