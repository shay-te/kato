"""Guards for the poisoned-resume failure mode.

Field bug: the operator switches the model mid-chat (e.g. fable → opus),
or a prior turn fails before a valid assistant message is written. The
resumed transcript's conversation continuation is now broken, so the
Claude CLI's next request carries an invalid ``previous_message_id`` and
the Anthropic API rejects EVERY resume of that session id with::

    API Error: 400 diagnostics.previous_message_id: must be the id from a
    prior /v1/messages response (starts with msg_)

Unlike a *stale* id (a live holder still owns the transcript — transient,
so kato keeps it pinned and retries), this corruption is PERMANENT: each
resume re-hits the same 400 and the chat is stuck forever. The only
recovery is a fresh session, so ``_resume_id_for_spawn`` ABANDONS the
poisoned id and spawns fresh.

Two things are tested here:

1. ``_died_with_poisoned_resume`` recognises the failure (and is
   conservative — never trips on a live session or a death without the
   distinctive marker).
2. ``start_session`` heals: the spawn after a poisoned death drops the
   resume id so the chat recovers instead of re-hitting the 400.
"""

from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager


_POISON_RESULT = (
    'API Error: 400 diagnostics.previous_message_id: must be the id from a '
    'prior /v1/messages response (starts with msg_)'
)


def _terminal(*, is_error: bool, result: str) -> SimpleNamespace:
    """A stand-in terminal SessionEvent — the detector only reads ``.raw``."""
    return SimpleNamespace(raw={'is_error': is_error, 'result': result})


class _StubSession:
    """Minimal StreamingClaudeSession surface with injectable death state."""

    def __init__(self, **kwargs):
        self._kwargs = kwargs
        self._task_id = kwargs.get('task_id', '')
        self._cwd = kwargs.get('cwd', '')
        self._agent_session_id = kwargs.get('resume_session_id', '') or 'fresh-id'
        self._alive = True
        self.terminated = False
        self.resume_confirmed = False
        self.resume_was_ignored = False
        self._terminal_event = None
        self._stderr: list[str] = []

    @property
    def task_id(self):
        return self._task_id

    @property
    def cwd(self):
        return self._cwd

    @property
    def agent_session_id(self):
        return self._agent_session_id

    @property
    def is_alive(self):
        return self._alive

    def start(self, *, initial_prompt=''):
        pass

    def stderr_snapshot(self):
        return list(self._stderr)

    @property
    def terminal_event(self):
        return self._terminal_event

    def terminate(self):
        self.terminated = True
        self._alive = False

    # ----- test helpers -----

    def die_poisoned_via_result(self):
        self._alive = False
        self._terminal_event = _terminal(is_error=True, result=_POISON_RESULT)

    def die_poisoned_via_stderr(self):
        self._alive = False
        self._stderr = ['some noise', _POISON_RESULT]


def _factory(instances):
    def build(**kwargs):
        session = _StubSession(**kwargs)
        instances.append(session)
        return session

    return build


def _manager(state_dir, factory):
    manager = ClaudeSessionManager(state_dir=state_dir, session_factory=factory)
    # Keep the registry holder-scan hermetic (it walks ~/.claude otherwise).
    manager._terminate_stale_resume_holders = mock.Mock()
    return manager


class DetectorTests(unittest.TestCase):
    """``_died_with_poisoned_resume`` — recognition + conservatism."""

    def test_exited_with_marker_in_result_is_poisoned(self) -> None:
        session = _StubSession()
        session.die_poisoned_via_result()
        self.assertTrue(ClaudeSessionManager._died_with_poisoned_resume(session))

    def test_exited_with_marker_in_stderr_is_poisoned(self) -> None:
        session = _StubSession()
        session.die_poisoned_via_stderr()
        self.assertTrue(ClaudeSessionManager._died_with_poisoned_resume(session))

    def test_live_session_is_never_poisoned(self) -> None:
        # A still-alive session that happens to surface the marker (e.g. a
        # tool echoing it) must NOT be treated as a poisoned death — that
        # would wrongly abandon a healthy conversation.
        session = _StubSession()
        session._terminal_event = _terminal(is_error=True, result=_POISON_RESULT)
        session._stderr = [_POISON_RESULT]
        self.assertTrue(session.is_alive)
        self.assertFalse(ClaudeSessionManager._died_with_poisoned_resume(session))

    def test_error_death_without_marker_is_not_poisoned(self) -> None:
        session = _StubSession()
        session._alive = False
        session._terminal_event = _terminal(
            is_error=True, result='API Error: 529 overloaded',
        )
        self.assertFalse(ClaudeSessionManager._died_with_poisoned_resume(session))

    def test_marker_in_non_error_result_is_not_poisoned(self) -> None:
        # The marker only counts as the failure when the turn errored.
        session = _StubSession()
        session._alive = False
        session._terminal_event = _terminal(is_error=False, result=_POISON_RESULT)
        self.assertFalse(ClaudeSessionManager._died_with_poisoned_resume(session))

    def test_stale_resume_death_is_not_poisoned(self) -> None:
        # "No conversation found" is the STALE path (kept pinned), not the
        # poisoned path (abandoned) — the two must stay distinct.
        session = _StubSession()
        session._alive = False
        session._stderr = ['No conversation found with session ID: fresh-id']
        self.assertFalse(ClaudeSessionManager._died_with_poisoned_resume(session))

    def test_clean_exit_is_not_poisoned(self) -> None:
        session = _StubSession()
        session._alive = False
        self.assertFalse(ClaudeSessionManager._died_with_poisoned_resume(session))


class HealTests(unittest.TestCase):
    """``start_session`` abandons a poisoned id and spawns fresh."""

    def _first_spawn(self, manager, instances):
        """Spawn once so the task pins 'fresh-id', and return that session."""
        session = manager.start_session(task_id='T1', cwd='/tmp/w')
        self.assertEqual(
            manager.get_record('T1').agent_session_id, 'fresh-id',
        )
        # The first spawn was a fresh one (no resume id requested).
        self.assertEqual(instances[0]._kwargs.get('resume_session_id'), '')
        return session

    def test_poisoned_resume_spawns_fresh_instead_of_resuming(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            instances: list[_StubSession] = []
            manager = _manager(td, _factory(instances))
            first = self._first_spawn(manager, instances)
            # The subprocess dies from the 400 on the model switch.
            first.die_poisoned_via_result()
            manager.start_session(task_id='T1', cwd='/tmp/w')
            # The heal: the SECOND spawn must NOT resume the poisoned id.
            self.assertEqual(len(instances), 2)
            self.assertEqual(instances[1]._kwargs.get('resume_session_id'), '')

    def test_poisoned_via_stderr_also_heals(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            instances: list[_StubSession] = []
            manager = _manager(td, _factory(instances))
            first = self._first_spawn(manager, instances)
            first.die_poisoned_via_stderr()
            manager.start_session(task_id='T1', cwd='/tmp/w')
            self.assertEqual(instances[1]._kwargs.get('resume_session_id'), '')

    def test_resume_id_for_spawn_returns_blank_for_poisoned(self) -> None:
        # Unit-level on the decision helper, independent of the spawn path.
        with tempfile.TemporaryDirectory() as td:
            manager = _manager(td, _factory([]))
            poisoned = _StubSession(resume_session_id='fresh-id')
            poisoned.die_poisoned_via_result()
            record = SimpleNamespace(agent_session_id='fresh-id')
            resume_id = manager._resume_id_for_spawn('T1', record, poisoned)
            self.assertEqual(resume_id, '')

    def test_non_poisoned_death_keeps_resume_id_pinned(self) -> None:
        # Regression: a death that is NOT poisoned must still return the
        # pinned id (the existing stale-but-kept-pinned behaviour).
        with tempfile.TemporaryDirectory() as td:
            manager = _manager(td, _factory([]))
            dead = _StubSession(resume_session_id='fresh-id')
            dead._alive = False
            record = SimpleNamespace(agent_session_id='fresh-id')
            resume_id = manager._resume_id_for_spawn('T1', record, dead)
            self.assertEqual(resume_id, 'fresh-id')


if __name__ == '__main__':
    unittest.main()
