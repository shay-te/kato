"""Sign in to Claude Code from kato's screen — no terminal.

``claude auth login`` opens the browser to sign in. With no terminal it also
prints a link (for when no browser opened — the usual case inside WSL) and then
waits on its input for a code ("Paste code here if prompted >"): the sign-in
page shows that code when it cannot hand it back by itself. So the CLI runs here
with a PIPE for input (never the operator's terminal), its output is kept for
the link, and a code the operator pastes into kato's screen is written to it.

One sign-in at a time. It ends when the CLI exits (0 = signed in), when it is
cancelled, or after ``TIMEOUT_SECONDS``. Whether the account is really signed
in is read from ``claude auth status`` (``agent_check``), not inferred here.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from collections.abc import Callable

TIMEOUT_SECONDS = 600
MAX_OUTPUT_CHARS = 8000
MAX_CODE_CHARS = 512
_LINK = re.compile(r'https://\S+')
_CODE = re.compile(r'^\S+$')


class SignInError(Exception):
    """A code kato will not write to the CLI, or no sign-in to write it to."""


class ClaudeSignIn(object):
    def __init__(
        self,
        *,
        spawn: Callable[..., subprocess.Popen] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
        timeout_seconds: float = TIMEOUT_SECONDS,
    ) -> None:
        self._spawn = spawn
        self._clock = clock
        self._timeout = timeout_seconds
        self._lock = threading.Lock()
        self._process = None
        self._output = ''
        self._state = 'idle'  # idle | waiting | done | failed | cancelled
        self._message = ''
        self._started_at = 0.0

    def start(self, binary: str) -> dict:
        """Run ``<binary> auth login``; a sign-in already under way is kept."""
        with self._lock:
            if self._state == 'waiting':
                return self._snapshot()
            try:
                process = self._spawn(
                    [binary or 'claude', 'auth', 'login'],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace',
                )
            except OSError as exc:
                self._state, self._message, self._output = 'failed', f'could not start {binary}: {exc}', ''
                return self._snapshot()
            self._process = process
            self._output, self._message = '', ''
            self._state = 'waiting'
            self._started_at = self._clock()
        threading.Thread(target=self._read, args=(process,), name='claude-sign-in', daemon=True).start()
        threading.Thread(target=self._watch, args=(process,), name='claude-sign-in-timeout', daemon=True).start()
        return self.status()

    def submit_code(self, code: str) -> dict:
        """Write the code the sign-in page showed to the waiting CLI."""
        code = str(code or '').strip()
        if not code or len(code) > MAX_CODE_CHARS or not _CODE.match(code):
            raise SignInError('paste the code exactly as the sign-in page shows it')
        with self._lock:
            process = self._process
            if self._state != 'waiting' or process is None or process.stdin is None:
                raise SignInError('no sign-in is waiting for a code — start it again')
            try:
                process.stdin.write(code + '\n')
                process.stdin.flush()
            except (OSError, ValueError) as exc:
                raise SignInError(f'the sign-in ended before the code arrived: {exc}') from exc
            return self._snapshot()

    def cancel(self) -> dict:
        with self._lock:
            if self._state == 'waiting' and self._process is not None:
                self._state, self._message = 'cancelled', 'sign-in cancelled'
                _kill(self._process)
            return self._snapshot()

    def status(self) -> dict:
        with self._lock:
            return self._snapshot()

    # ----- the CLI's side -----

    def _read(self, process) -> None:
        stream = process.stdout
        # Character by character: the prompt that asks for the code ends with
        # no newline, so reading lines would never deliver it.
        for char in iter(lambda: stream.read(1), ''):
            with self._lock:
                if len(self._output) < MAX_OUTPUT_CHARS:
                    self._output += char
        code = process.wait()
        _close_pipes(process)
        with self._lock:
            if self._process is not process or self._state != 'waiting':
                return
            if code == 0:
                self._state, self._message = 'done', 'signed in'
            else:
                self._state = 'failed'
                self._message = f'sign-in ended with exit code {code}'

    def _watch(self, process) -> None:
        while True:
            with self._lock:
                if self._process is not process or self._state != 'waiting':
                    return
                if self._clock() - self._started_at >= self._timeout:
                    self._state = 'failed'
                    self._message = 'the sign-in took too long — start it again'
                    _kill(process)
                    return
            time.sleep(0.5)

    def _snapshot(self) -> dict:
        link = _LINK.search(self._output)
        return {
            'state': self._state,
            'link': link.group(0) if link else '',
            # The prompt for a code is up: the page may be showing one.
            'asks_for_code': 'code' in self._output.lower() and self._state == 'waiting',
            'message': self._message,
        }


def _kill(process) -> None:
    try:
        process.kill()
    except OSError:
        pass


def _close_pipes(process) -> None:
    for stream in (process.stdin, process.stdout):
        try:
            if stream is not None:
                stream.close()
        except OSError:
            pass
