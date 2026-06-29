"""Background watcher that refreshes ``resume_prompt.md`` (and ``plan.md``)
after each Claude turn.

Polling-based by design — adds no event-callback infrastructure to
``claude_core_lib.session.streaming`` and never competes with the
SSE consumer for items off the live event queue. Each tick:

  1. Walk every session the manager owns.
  2. Snapshot ``session.recent_events()``.
  3. Compare ``len(events)`` (and the position of the newest
     ``result`` event) to the last-seen value.
  4. If a new turn ended, render a fresh ``resume_prompt.md``
     snapshot and atomic-write it at the task's workspace root.
  5. On every event change, capture any new ``ExitPlanMode`` plan and
     write ``plan.md`` so the UI can surface the plan for review. The
     plan is captured independently of the turn-end check (a plan-mode
     turn ends with the same ``result`` event), keyed off its own
     seen-state so an unchanged plan is never rewritten.

5-second tick is "after each turn" in practice — Claude turns end
at most a few per minute, so the operator sees the file fresh
within seconds of a turn finishing.

Thread-safe: the watcher runs on its own daemon thread; the dict of
last-seen state is only mutated from that thread.
"""
from __future__ import annotations

import threading

from agent_core_lib.agent_core_lib.helpers.session_id_utils import (
    read_session_id_from,
)
from kato_core_lib.helpers.logging_utils import configure_logger
from agent_core_lib.agent_core_lib.helpers.plan_capture_utils import (
    extract_plan_from_events,
)
from agent_core_lib.agent_core_lib.helpers.resume_prompt_utils import (
    build_inputs_from_session,
    render_resume_prompt,
)
from kato_core_lib.helpers.plan_writer import write_plan
from kato_core_lib.helpers.resume_prompt_writer import write_resume_prompt


# How often to poll live sessions. 5s gives "feels live" UX for the
# operator who refreshes the file in Cursor, without burning CPU on
# tasks where nothing is happening. The work per tick is cheap (a
# few list snapshots + at most one file write per task that had a
# new turn).
_DEFAULT_TICK_SECONDS: float = 5.0


class ResumePromptWatcher(object):
    """Owns the polling thread + per-task last-seen state.

    Started once at kato boot; runs until ``stop()`` is called or
    the process exits. Safe to instantiate without starting (tests
    can call ``tick()`` directly).
    """

    def __init__(
        self,
        *,
        session_manager,
        workspace_manager=None,
        tick_seconds: float = _DEFAULT_TICK_SECONDS,
    ) -> None:
        self._session_manager = session_manager
        self._workspace_manager = workspace_manager
        self._tick_seconds = max(0.5, float(tick_seconds))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # Per-task state: ``{lookup_key: (event_count, last_result_index)}``
        # so a turn that produced no new result event (e.g. just user
        # echoes) doesn't trigger a redundant rewrite.
        self._seen: dict[str, tuple[int, int]] = {}
        # Per-task hash of the last plan written, so an unchanged plan is
        # never rewritten (and ``plan.md``'s mtime only advances on a real
        # new plan — the UI auto-opens off that mtime).
        self._plan_seen: dict[str, str] = {}
        self.logger = configure_logger(self.__class__.__name__)

    # ----- lifecycle -----

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name='ResumePromptWatcher',
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.1, float(timeout)))
            self._thread = None

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception:
                # Never let one bad tick kill the watcher — the snapshot
                # logic touches live state from many threads.
                self.logger.exception('resume_prompt watcher tick failed')
            self._stop_event.wait(self._tick_seconds)

    # ----- one tick (extracted for tests) -----

    def tick(self) -> int:
        """Walk every known session once. Returns number of files written."""
        records_by_task = self._records_by_task()
        written = 0
        for task_id, session in self._list_sessions():
            written += self._process_session(task_id, session, records_by_task)
        return written

    def _process_session(self, task_id, session, records_by_task) -> int:
        """Handle one session: write ``plan.md`` on a new plan and
        ``resume_prompt.md`` on a fresh turn-end. Returns files written."""
        if session is None:
            return 0
        events = self._snapshot_events(task_id, session)
        if events is None:
            return 0
        seen_key = self._lookup_key(task_id)
        prev = self._seen.get(seen_key)
        last_result_index = _index_of_last_result(events)
        current = (len(events), last_result_index)
        if prev == current:
            return 0
        # Tick state advances even when nothing is written so we don't
        # busy-poll a stale session.
        workspace_path = self._workspace_path_for(task_id)
        written = 0
        if workspace_path:
            # Plan capture is independent of the resume-prompt turn-end
            # gate — it has its own seen-state keyed on the plan text.
            written += self._write_plan_if_new(seen_key, workspace_path, events)
            if _is_fresh_turn_end(prev, last_result_index):
                written += self._write_resume_prompt(
                    task_id, seen_key, workspace_path, events, records_by_task,
                )
        self._seen[seen_key] = current
        return written

    def _snapshot_events(self, task_id, session):
        try:
            return list(session.recent_events() or [])
        except Exception:
            self.logger.exception(
                'resume_prompt: failed to snapshot events for %s', task_id,
            )
            return None

    def _write_plan_if_new(self, seen_key, workspace_path, events) -> int:
        """Write ``plan.md`` when the captured plan differs from the last
        one written for this task. Returns 1 on write, else 0."""
        plan = extract_plan_from_events(events)
        if not plan or self._plan_seen.get(seen_key) == plan:
            return 0
        if write_plan(workspace_path, plan, logger=self.logger):
            self._plan_seen[seen_key] = plan
            return 1
        return 0

    def _write_resume_prompt(
        self, task_id, seen_key, workspace_path, events, records_by_task,
    ) -> int:
        record = records_by_task.get(seen_key)
        inputs = build_inputs_from_session(
            task_id=task_id,
            task_summary=(
                getattr(record, 'task_summary', '') if record else ''
            ),
            branch_name=(
                getattr(record, 'expected_branch', '') if record else ''
            ),
            workspace_path=str(workspace_path),
            repository_paths=self._repository_paths(task_id),
            recent_events=events,
            agent_session_id=read_session_id_from(record),
        )
        content = render_resume_prompt(inputs)
        if write_resume_prompt(workspace_path, content, logger=self.logger):
            return 1
        return 0

    # ----- session manager / workspace manager adapters -----
    # All defensive: any one of these may be None or missing fields
    # in tests / embedded use; the watcher must degrade silently.

    def _list_sessions(self) -> list[tuple[str, object]]:
        manager = self._session_manager
        if manager is None:
            return []
        list_records = getattr(manager, 'list_records', None)
        get_session = getattr(manager, 'get_session', None)
        if not callable(list_records) or not callable(get_session):
            return []
        try:
            records = list(list_records() or [])
        except Exception:
            return []
        out: list[tuple[str, object]] = []
        for record in records:
            task_id = str(getattr(record, 'task_id', '') or '')
            if not task_id:
                continue
            try:
                session = get_session(task_id)
            except Exception:
                session = None
            out.append((task_id, session))
        return out

    def _records_by_task(self) -> dict[str, object]:
        manager = self._session_manager
        if manager is None:
            return {}
        list_records = getattr(manager, 'list_records', None)
        if not callable(list_records):
            return {}
        try:
            records = list(list_records() or [])
        except Exception:
            return {}
        out: dict[str, object] = {}
        for record in records:
            task_id = str(getattr(record, 'task_id', '') or '')
            if task_id:
                out[self._lookup_key(task_id)] = record
        return out

    def _workspace_path_for(self, task_id: str):
        wm = self._workspace_manager
        if wm is None:
            return None
        get_path = getattr(wm, 'workspace_path', None)
        if not callable(get_path):
            return None
        try:
            return get_path(task_id)
        except Exception:
            return None

    def _repository_paths(self, task_id: str) -> list[str]:
        wm = self._workspace_manager
        if wm is None:
            return []
        get_workspace = getattr(wm, 'get', None)
        repo_path = getattr(wm, 'repository_path', None)
        if not callable(get_workspace) or not callable(repo_path):
            return []
        try:
            workspace = get_workspace(task_id)
        except Exception:
            return []
        if workspace is None:
            return []
        repo_ids = list(getattr(workspace, 'repository_ids', []) or [])
        paths: list[str] = []
        for rid in repo_ids:
            try:
                paths.append(str(repo_path(task_id, str(rid))))
            except Exception:
                continue
        return paths

    @staticmethod
    def _lookup_key(task_id: str) -> str:
        return str(task_id or '').strip().lower()


def _index_of_last_result(events: list) -> int:
    """Return the index of the newest ``result`` event, or -1 if none."""
    for i in range(len(events) - 1, -1, -1):
        if getattr(events[i], 'event_type', '') == 'result':
            return i
    return -1


def _is_fresh_turn_end(prev, last_result_index: int) -> bool:
    """True when a NEW turn just ended (a ``result`` event the previous
    snapshot hadn't seen). Guards the resume-prompt rewrite so a tick that
    only added non-result events (e.g. user echoes) doesn't rewrite."""
    if last_result_index < 0:
        return False
    return prev is None or last_result_index != prev[1]


# Convenience builder so callers wire the watcher in one line:
#   watcher = build_and_start_resume_prompt_watcher(app)
def build_and_start_resume_prompt_watcher(
    *,
    session_manager,
    workspace_manager=None,
    tick_seconds: float = _DEFAULT_TICK_SECONDS,
    autostart: bool = True,
) -> ResumePromptWatcher:
    watcher = ResumePromptWatcher(
        session_manager=session_manager,
        workspace_manager=workspace_manager,
        tick_seconds=tick_seconds,
    )
    if autostart:
        watcher.start()
    return watcher
