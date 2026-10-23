"""Notice tickets someone closed in the tracker, for the release notes.

Kato moves a ticket to done only when the operator says so (and that path
announces itself — ``TaskStateService.move_task_to_done``). The usual way work
finishes is different: the pull request is merged and a person closes the
ticket in YouTrack / Jira. Kato only sees that indirectly: the ticket leaves
the lists it polls (assigned to it, in review, owned by a live plan).

One job: once per cleanup sweep, compare that list with the last one, and for
each task that LEFT it, ask the tracker whether it is really done — leaving
the list also happens when a ticket is reassigned or cancelled. Only a
confirmed done is handed on.

Bounded on purpose:

* The first sweep ever (no state file) only records — enabling this never
  floods the notes with every task from before.
* At most ``MAX_PER_SWEEP`` tickets are asked about per sweep; the rest wait.
* "Could not ask" is retried on later sweeps, ``MAX_ATTEMPTS`` times at most.
* A tracker that cannot report a ticket's state (only YouTrack and Jira can)
  is skipped, said once in the log — there only "Finish & forget → done" and
  "Count as done" add entries.

What it remembers (``release_notes_watch.json``) survives a restart, so a
ticket closed while kato was down is still caught on the next sweep.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json

from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.logging_utils import configure_logger


class ReleaseNotesDoneWatcher(object):
    MAX_PER_SWEEP = 5
    MAX_ATTEMPTS = 3

    def __init__(
        self,
        *,
        task_service,
        task_state_service,
        on_done: Callable[[str], None],
        state_path: Path,
        is_local: Callable[[str], bool] = lambda task_id: False,
        is_forgotten: Callable[[str], bool] = lambda task_id: False,
        enabled: Callable[[], bool] = lambda: True,
        logger=None,
    ) -> None:
        self._get_task_service = provider_for(task_service)
        self._get_task_state_service = provider_for(task_state_service)
        self._on_done = on_done
        self._state_path = Path(state_path)
        self._is_local = is_local
        self._is_forgotten = is_forgotten
        self._enabled = enabled
        self._said_unsupported = False
        self.logger = logger or configure_logger('ReleaseNotesDoneWatcher')

    def observe(self, live_norm: set[str], workspace_ids: dict[str, str]) -> None:
        """One sweep: ``live_norm`` = lower-cased ids kato still works on;
        ``workspace_ids`` = lower-cased id → id for every task with a workspace."""
        try:
            self._sweep(set(live_norm), dict(workspace_ids))
        except Exception:
            self.logger.exception('release notes: could not check for tickets closed in the tracker')

    def _sweep(self, live_norm: set[str], workspace_ids: dict[str, str]) -> None:
        state = self._load()
        watched_now = sorted(norm for norm in live_norm if norm in workspace_ids)
        if state is None or not self._enabled():
            # First sweep ever, or switched off: remember where things stand
            # and add nothing — so switching it on never floods the notes.
            self._save({'live': watched_now, 'pending': {}})
            return
        pending: dict[str, dict] = {
            norm: entry for norm, entry in state.get('pending', {}).items() if norm not in live_norm
        }
        for norm in set(state.get('live', [])) - live_norm:
            original = workspace_ids.get(norm)
            if original and norm not in pending and not self._is_local(original) and not self._is_forgotten(original):
                pending[norm] = {'id': original, 'attempts': 0}
        if pending:
            self._confirm(pending)
        self._save({'live': watched_now, 'pending': pending})

    def _confirm(self, pending: dict[str, dict]) -> None:
        tracker = self._get_task_service().subtask_tracker()
        if tracker is None or not callable(getattr(tracker, 'get_issues_status', None)):
            if not self._said_unsupported:
                self.logger.info(
                    'release notes: this tracker cannot report a ticket\'s state, so tickets '
                    'closed in it are not added — "Finish & forget → done" still adds them',
                )
                self._said_unsupported = True
            pending.clear()
            return
        names = self._get_task_state_service().state_names()
        done = str(names.get('done', '') or '').strip().lower()
        batch = sorted(pending)[:self.MAX_PER_SWEEP]
        try:
            statuses = tracker.get_issues_status([pending[norm]['id'] for norm in batch], names.get('field', ''))
        except Exception as exc:
            self.logger.warning('release notes: could not read ticket states (%s); will retry', exc)
            self._count_attempt(pending, batch)
            return
        found = {str(task_id).lower(): status for task_id, status in (statuses or {}).items()}
        unanswered = []
        for norm in batch:
            status = found.get(norm)
            if status is None:
                unanswered.append(norm)
                continue
            state = str(getattr(status, 'state', '') or '').strip().lower()
            if getattr(status, 'exists', True) and done and state == done:
                self._on_done(pending[norm]['id'])
            del pending[norm]  # done, or left the list for another reason
        self._count_attempt(pending, unanswered)

    def _count_attempt(self, pending: dict[str, dict], norms: list[str]) -> None:
        for norm in norms:
            pending[norm]['attempts'] = int(pending[norm].get('attempts', 0)) + 1
            if pending[norm]['attempts'] >= self.MAX_ATTEMPTS:
                self.logger.info(
                    'release notes: giving up on %s — its state could not be read', pending[norm]['id'],
                )
                del pending[norm]

    def _load(self) -> dict | None:
        try:
            data = json.loads(self._state_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return {'live': [], 'pending': {}}
        if not isinstance(data, dict):
            return {'live': [], 'pending': {}}
        return {
            'live': [str(item) for item in data.get('live', []) if isinstance(item, str)],
            'pending': {
                str(norm): entry for norm, entry in (data.get('pending') or {}).items()
                if isinstance(entry, dict) and entry.get('id')
            },
        }

    def _save(self, state: dict) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # the write below says what went wrong
        atomic_write_json(self._state_path, state, logger=self.logger, label='release notes watch list')
