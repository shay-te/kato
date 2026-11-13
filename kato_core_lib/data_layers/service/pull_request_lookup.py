"""Which pull requests a branch has — asked of the git host as rarely as it can be.

THE one place kato asks a git host for a branch's pull requests
(``RepositoryService.find_pull_requests`` / ``pull_request_states`` both come
here; ``tests/test_pull_request_lookup.py`` reads the source and fails when
anything else asks the host directly).

Why it exists: the review scan (every 50s) searched the host once for every
repository of every task In Review whose ticket does not link a pull request —
and most of a many-repository task's repositories have none. UNA-3211's twenty
repositories were twenty Bitbucket calls a scan, forever: endless 429s, 15-22s
retry sleeps inside the scan, and a traceback that dropped the task's other
repositories with it.

* **An answer is remembered** per (repository, branch, title prefix, state) for
  ``ANSWER_TTL_SECONDS`` — "no pull request" too. A pull request kato opens
  itself is never waited for: ``forget`` drops the branch's answers the moment
  it is created. One opened outside kato is found within the TTL.
* **A host that says 429 pauses every lookup on that host**
  (``Retry-After`` if it says, else ``DEFAULT_PAUSE_SECONDS``): the next
  nineteen repositories do not fire into the same limit. While paused a
  remembered answer — even an old one — is used; with none, ``LookupPaused`` is
  raised: "could not ask" is never "no pull request".
* ``cached=False`` (an operator creating a pull request — it must not open a
  duplicate — and the merge probes) always asks, but still never during a
  pause.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from kato_core_lib.data_layers.data.fields import RepositoryFields
from utils_core_lib.utils_core_lib.text_utils import normalized_text, text_from_attr

#: How long an answer — a pull request found, or none — is used before asking again.
ANSWER_TTL_SECONDS = 600.0
#: A 429 without a usable ``Retry-After``: how long the host is left alone.
DEFAULT_PAUSE_SECONDS = 120.0
#: A ``Retry-After`` above this is clamped: kato must come back within the hour.
MAX_PAUSE_SECONDS = 900.0

Ask = Callable[..., list]


class LookupPaused(RuntimeError):
    """The host asked kato to slow down and nothing remembered answers this."""

    def __init__(self, host: str, seconds_left: float) -> None:
        self.host = host
        self.seconds_left = max(0.0, seconds_left)
        super().__init__(
            f'{host or "the git host"} asked kato to slow down (HTTP 429); '
            f'not asking it again for {max(1, math.ceil(self.seconds_left))}s'
        )


@dataclass(frozen=True)
class _Answer(object):
    pull_requests: tuple
    at: float


def rate_limit_pause_seconds(error: BaseException) -> float:
    """How long to leave the host alone after ``error``; 0 when it is no 429.

    Reads the HTTP response the error carries (``requests`` puts it on
    ``.response``), following ``__cause__`` / ``__context__`` — a wrapper
    around the 429 is still a 429."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        response = getattr(current, 'response', None)
        if getattr(response, 'status_code', None) == 429:
            return _retry_after(getattr(response, 'headers', None))
        current = current.__cause__ or current.__context__
    return 0.0


def _retry_after(headers) -> float:
    try:
        seconds = float((headers or {}).get('Retry-After'))
    except (TypeError, ValueError, AttributeError):
        return DEFAULT_PAUSE_SECONDS
    if seconds <= 0:
        return DEFAULT_PAUSE_SECONDS
    return min(seconds, MAX_PAUSE_SECONDS)


def host_of(repository) -> str:
    """The host a repository's pull request API lives on — what a 429 pauses."""
    return normalized_text(text_from_attr(repository, RepositoryFields.PROVIDER_BASE_URL)).lower()


class PullRequestLookup(object):
    def __init__(
        self,
        ask: Ask,
        *,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = ANSWER_TTL_SECONDS,
    ) -> None:
        self._ask = ask
        self._clock = clock
        self._ttl = float(ttl_seconds)
        self._lock = threading.Lock()
        self._answers: dict[tuple, _Answer] = {}
        self._paused_until: dict[str, float] = {}

    def find(
        self,
        repository,
        *,
        source_branch: str = '',
        title_prefix: str = '',
        state: str = '',
        cached: bool = True,
    ) -> list[dict[str, str]]:
        """The branch's pull requests: remembered when ``cached`` and young
        enough, else asked. Raises ``LookupPaused`` while the host is paused
        and nothing remembered answers; raises what the host raised."""
        key = _key(repository, source_branch, title_prefix, state)
        host = host_of(repository)
        now = self._clock()
        with self._lock:
            answer = self._answers.get(key)
            if cached and answer is not None and now - answer.at < self._ttl:
                return _copies(answer)
            paused_until = self._paused_until.get(host, 0.0)
            if now < paused_until:
                if cached and answer is not None:
                    return _copies(answer)  # old, but "could not ask" is not "none"
                raise LookupPaused(host, paused_until - now)
        try:
            found = self._ask(
                repository, source_branch=source_branch, title_prefix=title_prefix, state=state,
            )
        except Exception as error:
            pause = rate_limit_pause_seconds(error)
            if pause:
                with self._lock:
                    self._paused_until[host] = max(
                        self._paused_until.get(host, 0.0), self._clock() + pause,
                    )
            raise
        pull_requests = tuple(dict(pull_request) for pull_request in (found or []))
        with self._lock:
            self._answers[key] = _Answer(pull_requests, self._clock())
        return [dict(pull_request) for pull_request in pull_requests]

    def forget(self, repository, source_branch: str) -> None:
        """Drop every remembered answer for the branch (kato just opened a
        pull request from it): the next lookup asks."""
        repository_id = _repository_id(repository)
        branch = normalized_text(source_branch)
        with self._lock:
            for key in [key for key in self._answers if key[:2] == (repository_id, branch)]:
                del self._answers[key]

    def paused_for(self, repository) -> float:
        """Seconds the repository's host is still paused (0 = not paused)."""
        with self._lock:
            return max(0.0, self._paused_until.get(host_of(repository), 0.0) - self._clock())


def _key(repository, source_branch: str, title_prefix: str, state: str) -> tuple:
    return (
        _repository_id(repository), normalized_text(source_branch),
        normalized_text(title_prefix), normalized_text(state).lower(),
    )


def _repository_id(repository) -> str:
    return normalized_text(getattr(repository, 'id', '')).lower()


def _copies(answer: _Answer) -> list[dict[str, str]]:
    return [dict(pull_request) for pull_request in answer.pull_requests]
