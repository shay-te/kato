"""Flow #10 — Claude rejects ``--resume <id>`` and kato self-heals.

A-Z scenario:

    1. kato has a persisted session id for task T1, e.g. ``dead-id``.
    2. The corresponding JSONL is gone — operator wiped ``~/.claude``,
       machine moved, Anthropic CLI cleared the project dir.
    3. Operator sends a message → kato spawns ``claude --resume dead-id``.
    4. Claude exits ~immediately with
       ``No conversation found with session ID: dead-id``.
    5. kato detects the stale-resume failure during a short poll window,
       terminates the dead subprocess, clears the stale id from the
       persisted record, and respawns FRESH (no ``--resume`` flag).
    6. Operator sees a fresh session — a single conversation, not a loop.

Why this matters: without the self-heal, every scan cycle would
respawn against the dead id, every spawn would die the same way,
and the tab would loop indefinitely or burn tokens forever. This is
a *silent* failure mode — the operator might not even notice token
burn until a bill arrives.

Three detection contracts have to hold together:
    A) ``_died_with_stale_resume_id`` recognizes the marker in stderr.
    B) ``_died_with_stale_resume_id`` ALSO recognizes the marker in
       the terminal result event (fallback path).
    C) ``_wait_for_stale_resume_failure`` polls within a bounded window
       so kato doesn't hang waiting for healthy spawns.

Plus a persistence contract:
    D) ``_resume_id_for_spawn`` clears the stale id from the on-disk
       record BEFORE the next spawn, so the next scan doesn't repeat
       the doomed --resume.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace


# ---------------------------------------------------------------------------
# Helpers — different stub shapes for the various detection paths.
# ---------------------------------------------------------------------------


def _stub_session_with_stderr(lines):
    """A stub that surfaces stderr lines, mirroring StreamingClaudeSession."""
    s = SimpleNamespace()
    s.is_alive = False
    s.stderr_snapshot = lambda: list(lines)
    s.terminal_event = None
    return s


def _stub_session_with_terminal_event(raw):
    """A stub that surfaces only the terminal_event path (no stderr)."""
    s = SimpleNamespace()
    s.is_alive = False
    s.stderr_snapshot = lambda: []
    s.terminal_event = SimpleNamespace(raw=raw)
    return s


def _stub_session_alive_then_die(die_at_call: int):
    """Stub that reports alive for the first N polls, then dead with marker.
    Used to exercise the polling loop in _wait_for_stale_resume_failure."""
    state = {'calls': 0}

    def is_alive_getter():
        state['calls'] += 1
        return state['calls'] < die_at_call

    s = SimpleNamespace()
    # is_alive needs to be a property-like getter; SimpleNamespace can't
    # do that natively, so we wrap in a descriptor-friendly object.

    class _S:
        @property
        def is_alive(self):
            state['calls'] += 1
            return state['calls'] < die_at_call

        def stderr_snapshot(self):
            return [] if state['calls'] < die_at_call else [
                'No conversation found with session ID: bad-id',
            ]

        terminal_event = None

    return _S()


# ---------------------------------------------------------------------------
# Detection contracts (A) and (B).
# ---------------------------------------------------------------------------


class FlowStaleResumeDetectionTests(unittest.TestCase):

    def test_flow_stale_resume_detected_from_stderr_marker(self) -> None:
        # Contract A: the canonical CLI error in stderr triggers detection.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        session = _stub_session_with_stderr([
            'some unrelated chatter',
            'No conversation found with session ID: bad-id',
            'more unrelated chatter',
        ])
        self.assertTrue(
            ClaudeSessionManager._died_with_stale_resume_id(session, 'bad-id'),
            'detection missed the marker line — self-heal would never fire',
        )

    def test_flow_stale_resume_detected_from_terminal_event_when_no_stderr(self) -> None:
        # Contract B: stderr can be empty (CLI piped output) but the
        # terminal event carries the same marker in ``result``. Lock
        # that fallback path.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        session = _stub_session_with_terminal_event({
            'is_error': True,
            'result': 'No conversation found with session ID: bad-id',
        })
        self.assertTrue(
            ClaudeSessionManager._died_with_stale_resume_id(session, 'bad-id'),
            'terminal-event fallback missed the marker',
        )

    def test_flow_stale_resume_negative_when_terminal_is_not_error(self) -> None:
        # The terminal event may quote the marker for unrelated reasons
        # (e.g., debug logs). The detector MUST require ``is_error`` =
        # True before parsing the result text. Otherwise self-heal fires
        # on a perfectly-healthy session.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        session = _stub_session_with_terminal_event({
            'is_error': False,
            'result': 'No conversation found with session ID: bad-id',
        })
        self.assertFalse(
            ClaudeSessionManager._died_with_stale_resume_id(session, 'bad-id'),
            'detector misfired on a non-error terminal event',
        )

    def test_flow_stale_resume_negative_when_no_terminal_event(self) -> None:
        # If the session is alive (or just spawning) with no terminal
        # event yet, detection must return False — not crash on None.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        session = SimpleNamespace(
            is_alive=True,
            stderr_snapshot=lambda: [],
            terminal_event=None,
        )
        self.assertFalse(
            ClaudeSessionManager._died_with_stale_resume_id(session, 'bad-id'),
        )

    def test_flow_stale_resume_negative_with_different_session_id(self) -> None:
        # The marker has to match the exact resume id we passed.
        # A leftover marker from a DIFFERENT session id (e.g., the CLI
        # printed about session 'X' but kato passed 'Y') must NOT
        # trigger self-heal — that would cascade-fail healthy sessions.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        session = _stub_session_with_stderr([
            'No conversation found with session ID: SOMEONE-ELSE',
        ])
        self.assertFalse(
            ClaudeSessionManager._died_with_stale_resume_id(session, 'bad-id'),
            'detection fired on a different session id — cross-contamination risk',
        )

    def test_flow_stale_resume_negative_on_clean_exit(self) -> None:
        # A normal session that finished its turn and exited (no marker
        # anywhere) must NOT trigger self-heal — that would force a
        # spurious respawn.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        session = _stub_session_with_stderr([
            'INFO: turn finished',
        ])
        self.assertFalse(
            ClaudeSessionManager._died_with_stale_resume_id(session, 'bad-id'),
        )

    def test_flow_stale_resume_tolerates_stderr_snapshot_exceptions(self) -> None:
        # Robustness: if ``stderr_snapshot`` raises (subprocess pipe in
        # an odd state), detection must continue and consult the
        # terminal-event fallback rather than crashing the spawn flow.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )

        class _Broken:
            is_alive = False
            terminal_event = SimpleNamespace(raw={
                'is_error': True,
                'result': 'No conversation found with session ID: bad-id',
            })

            def stderr_snapshot(self):
                raise OSError('pipe closed')

        session = _Broken()
        self.assertTrue(
            ClaudeSessionManager._died_with_stale_resume_id(session, 'bad-id'),
            'stderr_snapshot exception broke the terminal-event fallback',
        )


# ---------------------------------------------------------------------------
# Polling contract (C): bounded wait window.
# ---------------------------------------------------------------------------


class FlowStaleResumePollingTests(unittest.TestCase):

    def test_flow_stale_resume_poll_returns_true_when_marker_appears(self) -> None:
        # Spawn fails right away — the poll loop's first iteration
        # detects the marker.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )

        class _Dead:
            is_alive = False
            terminal_event = None

            def stderr_snapshot(self):
                return ['No conversation found with session ID: dead-id']

        result = ClaudeSessionManager._wait_for_stale_resume_failure(
            _Dead(), 'dead-id', max_wait_seconds=0.5, poll_interval_seconds=0.05,
        )
        self.assertTrue(result)

    def test_flow_stale_resume_poll_returns_false_on_timeout(self) -> None:
        # Healthy session that doesn't emit the marker before the
        # deadline: poll returns False. The orchestrator carries on
        # and lets the session run normally.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )

        class _Healthy:
            is_alive = True
            terminal_event = None

            def stderr_snapshot(self):
                return []

        start = time.monotonic()
        result = ClaudeSessionManager._wait_for_stale_resume_failure(
            _Healthy(), 'dead-id',
            max_wait_seconds=0.15,
            poll_interval_seconds=0.05,
        )
        elapsed = time.monotonic() - start
        self.assertFalse(result)
        # Bounded wait: must finish within ~max_wait_seconds + one poll.
        self.assertLess(
            elapsed, 1.0,
            'poll wait did not respect the timeout — kato would hang on '
            'every healthy spawn',
        )

    def test_flow_stale_resume_poll_zero_timeout_returns_immediately(self) -> None:
        # Defensive: if a caller passes 0 (or negative), the loop must
        # not enter at all. Returns False, doesn't hang.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )

        class _Healthy:
            is_alive = True
            terminal_event = None

            def stderr_snapshot(self):
                return []

        result = ClaudeSessionManager._wait_for_stale_resume_failure(
            _Healthy(), 'dead-id', max_wait_seconds=0.0,
        )
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# Persistence contract (D): blank the stale id BEFORE next spawn.
# ---------------------------------------------------------------------------


class FlowStaleResumePersistenceTests(unittest.TestCase):

    def test_flow_stale_resume_blanks_id_on_record_before_returning(self) -> None:
        # ``_resume_id_for_spawn`` is called BEFORE spawn. If self-heal
        # fires here, the record MUST be persisted with a blank id so
        # the next scan doesn't repeat the doomed --resume.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager, PlanningSessionRecord,
            SESSION_STATUS_TERMINATED,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=lambda **_: None,
            )
            record = PlanningSessionRecord(
                task_id='T1',
                claude_session_id='dead-id',
                status=SESSION_STATUS_TERMINATED,
            )
            with mgr._lock:
                mgr._records['T1'] = record

            # Existing-session stub: dead, with the marker in stderr.
            existing = _stub_session_with_stderr([
                'No conversation found with session ID: dead-id',
            ])

            # Trigger the self-heal decision path.
            result_id = mgr._resume_id_for_spawn('T1', record, existing)

            self.assertEqual(
                result_id, '',
                'self-heal did not clear the resume id — next spawn '
                'will try the dead id again and loop',
            )
            # Disk-side: record was persisted with the blank.
            persisted = json.loads(
                (Path(state_dir) / 'T1.json').read_text(encoding='utf-8'),
            )
            self.assertEqual(
                persisted['claude_session_id'], '',
                'on-disk record still carries the dead id — kato restart '
                'will respawn against it forever',
            )

    def test_flow_stale_resume_no_persist_on_healthy_session(self) -> None:
        # Negative case: healthy existing session, no marker, no
        # self-heal. The resume id must come back unchanged.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager, PlanningSessionRecord,
            SESSION_STATUS_TERMINATED,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=lambda **_: None,
            )
            record = PlanningSessionRecord(
                task_id='T1',
                claude_session_id='healthy-id',
                status=SESSION_STATUS_TERMINATED,
            )
            with mgr._lock:
                mgr._records['T1'] = record

            healthy = SimpleNamespace(
                is_alive=True,
                stderr_snapshot=lambda: [],
                terminal_event=None,
            )

            result_id = mgr._resume_id_for_spawn('T1', record, healthy)
            self.assertEqual(result_id, 'healthy-id')

    def test_flow_stale_resume_with_no_previous_record_returns_empty(self) -> None:
        # First-ever spawn: no previous record. Resume id is empty by
        # definition; the self-heal short-circuit must skip.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=lambda **_: None,
            )
            self.assertEqual(
                mgr._resume_id_for_spawn('T1', None, None), '',
            )

    def test_flow_stale_resume_with_no_existing_session_uses_record_id(self) -> None:
        # Record has an id, but there's no existing session to inspect
        # (terminate happened cleanly). Without an existing session,
        # self-heal can't fire — return the id as-is and let the SPAWN
        # path detect a runtime failure.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager, PlanningSessionRecord,
            SESSION_STATUS_TERMINATED,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=lambda **_: None,
            )
            record = PlanningSessionRecord(
                task_id='T1',
                claude_session_id='not-yet-validated',
                status=SESSION_STATUS_TERMINATED,
            )
            self.assertEqual(
                mgr._resume_id_for_spawn('T1', record, None),
                'not-yet-validated',
            )


# ---------------------------------------------------------------------------
# Full A-Z: end-to-end via start_session with a self-healing factory.
# ---------------------------------------------------------------------------


class FlowStaleResumeEndToEndTests(unittest.TestCase):

    def test_flow_stale_resume_end_to_end_dead_id_yields_fresh_session(self) -> None:
        # The full flow inside ``_spawn_with_resume_self_heal``: spawn
        # with stale id → marker detected → terminate → respawn fresh
        # → return the fresh session.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )

        # Factory returns a session that "dies" immediately with the
        # stale-resume marker on the FIRST call (the spawn with --resume),
        # and a healthy session on the SECOND call (the fresh respawn).
        spawn_calls = []

        def factory(**kwargs):
            spawn_calls.append(kwargs)
            if kwargs.get('resume_session_id') == 'dead-id':
                # First spawn: dies with marker.
                class _Dead:
                    is_alive = False
                    terminal_event = None

                    @property
                    def claude_session_id(self): return 'dead-id'

                    def start(self, *, initial_prompt=''): pass

                    def stderr_snapshot(self):
                        return [
                            'No conversation found with session ID: dead-id',
                        ]

                    def terminate(self): pass

                return _Dead()

            # Second spawn: fresh, healthy.
            class _Fresh:
                is_alive = True
                terminal_event = None

                @property
                def claude_session_id(self): return 'brand-new-uuid'

                def start(self, *, initial_prompt=''): pass

                def stderr_snapshot(self): return []

                def terminate(self): pass

            return _Fresh()

        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=factory,
            )
            session = mgr._spawn_with_resume_self_heal(
                normalized_task_id='T1',
                factory_kwargs={'task_id': 'T1'},
                initial_prompt='go',
                resume_session_id='dead-id',
            )
            # Two spawns happened: stale, then fresh.
            self.assertEqual(len(spawn_calls), 2)
            self.assertEqual(spawn_calls[0].get('resume_session_id'), 'dead-id')
            self.assertEqual(spawn_calls[1].get('resume_session_id'), '')
            # Returned session is the fresh one.
            self.assertEqual(session.claude_session_id, 'brand-new-uuid')

    def test_flow_stale_resume_end_to_end_healthy_resume_no_respawn(self) -> None:
        # Negative end-to-end: a healthy resume must NOT respawn.
        # Otherwise we'd double every spawn, halving throughput.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )

        spawn_calls = []

        def factory(**kwargs):
            spawn_calls.append(kwargs)

            class _Healthy:
                is_alive = True
                terminal_event = None

                @property
                def claude_session_id(self):
                    return kwargs.get('resume_session_id', '') or 'fresh-id'

                def start(self, *, initial_prompt=''): pass

                def stderr_snapshot(self): return []

                def terminate(self): pass

            return _Healthy()

        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=factory,
            )
            session = mgr._spawn_with_resume_self_heal(
                normalized_task_id='T1',
                factory_kwargs={'task_id': 'T1'},
                initial_prompt='go',
                resume_session_id='healthy-id',
            )
            self.assertEqual(len(spawn_calls), 1)
            self.assertEqual(session.claude_session_id, 'healthy-id')


if __name__ == '__main__':
    unittest.main()
