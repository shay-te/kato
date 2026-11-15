"""Kato's ONE status line in the terminal: rewritten in place, never a new line.

A scan every 50 seconds printed "Scanning for new tasks and reviews" and
"Scan complete" as two new lines each time — a terminal full of them by noon
("no need to print it millions of times, use a rotating progress"). What kato
is doing between real events now spins on one line: "Scanning … /" while a
scan runs, "Idle · next scan in 45 -" between scans.

A real log line still prints as its own line: the console handlers
(``StatusLineAwareHandler``) clear the status line first, so a line from
another thread never lands glued onto the spinner, and the spinner paints
itself again on its next frame.

Only on a terminal (``supports_inline_status``); a redirected stream gets
nothing — not even the scan lines, which the UI's feed still shows.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from contextlib import contextmanager
from math import ceil

_FRAMES = ('/', '-', '\\', '|')
_FRAME_INTERVAL_SECONDS = 0.2

# What the status line holds now: the stream it is painted on and how wide it
# is (0 = nothing on it). One lock for the line and every console write, so a
# log line and a spinner frame can never interleave mid-write.
_LINE_LOCK = threading.RLock()
_line = {'stream': None, 'width': 0}


def supports_inline_status(stream=None) -> bool:
    output_stream = sys.stderr if stream is None else stream
    isatty = getattr(output_stream, 'isatty', None)
    return bool(callable(isatty) and isatty())


def show_inline_status(text: str, stream=None) -> None:
    """Paint ``text`` on the status line, over whatever it held."""
    output_stream = sys.stderr if stream is None else stream
    with _LINE_LOCK:
        previous = _line['width'] if _line['stream'] is output_stream else 0
        output_stream.write('\r' + text + ' ' * max(0, previous - len(text)))
        output_stream.flush()
        _line['stream'] = output_stream
        _line['width'] = len(text)


def clear_inline_status(stream, *, status_text: str = '') -> None:
    with _LINE_LOCK:
        shown = _line['width'] if _line['stream'] is stream else 0
        clear_width = max(40, len(status_text) + 2, shown)
        stream.write('\r' + (' ' * clear_width) + '\r')
        stream.flush()
        if _line['stream'] is stream:
            _line['width'] = 0


class StatusLineAwareHandler(logging.StreamHandler):
    """A console handler that takes the status line off before it prints."""

    def emit(self, record: logging.LogRecord) -> None:
        with _LINE_LOCK:
            if _line['width'] and _line['stream'] is self.stream:
                clear_inline_status(self.stream)
            super().emit(record)


@contextmanager
def spinning_status(text: str, *, stream=None, frame_seconds: float = _FRAME_INTERVAL_SECONDS):
    """Spin ``text`` on the status line while the block runs; clear it after.

    A thread draws the frames, so the block itself runs untouched. Nothing at
    all happens when the stream is not a terminal.
    """
    output_stream = sys.stderr if stream is None else stream
    if not supports_inline_status(output_stream):
        yield
        return
    stop = threading.Event()

    def spin() -> None:
        frame_index = 0
        while not stop.is_set():
            show_inline_status(f'{text} {_FRAMES[frame_index % len(_FRAMES)]}', output_stream)
            frame_index += 1
            stop.wait(frame_seconds)

    spinner = threading.Thread(target=spin, name='status-line', daemon=True)
    spinner.start()
    try:
        yield
    finally:
        stop.set()
        spinner.join(timeout=1.0)
        clear_inline_status(output_stream, status_text=text)


def _sleep_with_inline_spinner(
    total_seconds: float,
    *,
    render_frame,
    clear_text: str,
    sleep_fn=time.sleep,
    stream=None,
) -> None:
    """Sleep for ``total_seconds`` while animating an inline spinner.

    ``render_frame(frame, remaining_seconds, frame_index)`` returns the
    text painted on the status line each tick; ``clear_text`` sizes the final
    clear. Falls back to a plain ``sleep_fn`` when the stream can't show
    inline status, and is a no-op for non-positive durations.
    """
    if total_seconds <= 0:
        return

    output_stream = sys.stderr if stream is None else stream
    if not supports_inline_status(output_stream):
        sleep_fn(total_seconds)
        return

    remaining_seconds = float(total_seconds)
    frame_index = 0
    while remaining_seconds > 0:
        frame = _FRAMES[frame_index % len(_FRAMES)]
        show_inline_status(render_frame(frame, remaining_seconds, frame_index), output_stream)
        sleep_duration = min(_FRAME_INTERVAL_SECONDS, remaining_seconds)
        sleep_fn(sleep_duration)
        remaining_seconds -= sleep_duration
        frame_index += 1
    clear_inline_status(output_stream, status_text=clear_text)


def sleep_with_countdown_spinner(
    total_seconds: float,
    *,
    status_text: str,
    sleep_fn=time.sleep,
    stream=None,
    countdown_seconds: int | None = None,
) -> None:
    """Sleep for ``total_seconds`` while spinning an inline countdown.

    By default the displayed number is derived from the remaining sleep,
    so a 30s sleep ticks 30→1. Callers that drive their own outer loop
    (e.g. ``_idle_with_heartbeat``) sleep in ~1s chunks but want the
    spinner to show the *outer* countdown — pass ``countdown_seconds`` to
    pin the displayed value for this call.
    """
    def render(frame, remaining_seconds, frame_index):
        if countdown_seconds is None:
            displayed = max(1, int(ceil(remaining_seconds)))
        else:
            displayed = max(0, int(countdown_seconds))
        return f'{status_text} {frame} {displayed}'

    _sleep_with_inline_spinner(
        total_seconds,
        render_frame=render,
        clear_text=f'{status_text} 999',
        sleep_fn=sleep_fn,
        stream=stream,
    )
