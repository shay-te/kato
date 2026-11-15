"""Say a standing condition ONCE — again only when it changes, once when it ends.

The scan runs every 50 seconds, and a problem that lasts (a token Bitbucket
refuses, tasks the operator deleted) was printed again on every scan: twenty
identical 403 lines per round for UNA-3211's twenty repositories, and "review
scan skipped 10 forgotten task(s)" 38 times in 400 lines (2026-11-15, "why
it's printing so much duplicates?"). The banner already shows what lasts; the
terminal is for what HAPPENS.

``SaidOnce.say(key, text)`` answers whether to say ``text`` now: yes the first
time and whenever it differs from the last text said for ``key``. The caller
keeps ``text`` stable while nothing changes (no counters, no timestamps, no
per-request URLs in it). ``ended(key)`` answers whether something was said that
is now over, so the caller can say so once.
"""

from __future__ import annotations

import threading


class SaidOnce(object):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._said: dict[str, str] = {}

    def say(self, key: str, text: str) -> bool:
        """True when ``text`` is news for ``key`` (and it is remembered as said)."""
        with self._lock:
            if self._said.get(key) == text:
                return False
            self._said[key] = text
            return True

    def ended(self, key: str) -> bool:
        """True when something was said for ``key`` — it is forgotten now."""
        with self._lock:
            return self._said.pop(key, None) is not None
