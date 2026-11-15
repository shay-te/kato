"""What kato could not reach on its last try — the ticket system, a git host.

A scan that cannot fetch the tasks fails into the log, and a git host that
refuses a pull request lookup is one warning line; the screen said nothing
("if there is a fail to fetch from Jira the UI says nothing"). Every place
kato asks one of those services reports here: ``failed`` with the service's
own words, ``succeeded`` once it answers again. ``problems()`` is what is
failing right now, for the screen (the config status payload → a banner),
until it answers again.

One instance per kato, shared by the scan (the ticket system) and the review
scan (each git host). In memory: a restart starts clean, and the next scan
reports again.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

TRACKER = 'tracker'
MAX_ERROR_CHARS = 2000

PROVIDER_LABELS = {
    'youtrack': 'YouTrack',
    'jira': 'Jira',
    'github': 'GitHub',
    'gitlab': 'GitLab',
    'bitbucket': 'Bitbucket',
    'azure': 'Azure DevOps',
    'azure_devops': 'Azure DevOps',
    'claude': 'Claude Code',
    'codex': 'Codex',
    'openhands': 'OpenHands',
}


def provider_label(name: str) -> str:
    text = str(name or '').strip()
    return PROVIDER_LABELS.get(text.lower(), text or 'a service')


def git_host_key(provider: str) -> str:
    return f'git:{str(provider or "").strip().lower() or "unknown"}'


class ConnectionHealth(object):
    def __init__(self, *, tracker_label: str = '', clock: Callable[[], float] = time.time) -> None:
        self.tracker_label = provider_label(tracker_label) if tracker_label else 'the ticket system'
        self._clock = clock
        self._lock = threading.Lock()
        self._failing: dict[str, dict] = {}

    def failed(self, key: str, *, label: str, kind: str, error: object) -> None:
        """``key`` could not be reached; ``error`` is what it (or the network) said."""
        text = (str(error or '').strip() or error.__class__.__name__)[:MAX_ERROR_CHARS]
        now = self._clock()
        with self._lock:
            entry = self._failing.get(key)
            if entry is None:
                entry = {'key': key, 'label': label, 'kind': kind, 'since': now, 'failures': 0}
                self._failing[key] = entry
            entry.update(label=label, kind=kind, error=text, last=now, failures=entry['failures'] + 1)

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._failing.pop(key, None)

    def problems(self) -> list[dict]:
        """What is failing right now, the longest-failing first."""
        with self._lock:
            entries = [dict(entry) for entry in self._failing.values()]
        return sorted(entries, key=lambda entry: (entry['since'], entry['key']))


def report(health: ConnectionHealth | None, key: str, *, error: object = None, label: str = '', kind: str = '') -> None:
    """Report one try to ``health`` (None: nothing is listening) — ``error``
    None is a success. Never raises: reporting must not break a scan."""
    if health is None:
        return
    try:
        if error is None:
            health.succeeded(key)
        else:
            health.failed(key, label=label, kind=kind, error=error)
    except Exception:  # noqa: BLE001 - the scan matters more than the report
        pass


# The startup check's steps under the keys the rest of kato uses: the tracker
# step IS the scan's ``tracker`` entry (a scan that later gets through clears
# it, and the other way round).
STARTUP_STEP_KEYS = {
    'tracker': (TRACKER, 'tracker'),
    'repositories': ('repositories', 'repositories'),
}


def report_startup_step(health: ConnectionHealth | None, kind: str, service_name: str, error: object) -> None:
    """One startup-check step's outcome (``startup_dependency_validator``)."""
    key, problem_kind = STARTUP_STEP_KEYS.get(kind, (f'agent:{service_name}', 'settings'))
    label = 'your repositories' if kind == 'repositories' else provider_label(service_name)
    report(health, key, error=error, label=label, kind=problem_kind)
