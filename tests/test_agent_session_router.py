"""Routing live chats to the backend that owns them.

The webserver and the orchestrator hold ONE session manager and call ten
methods on it. Teaching every one of those call sites which CLI a task belongs
to would be twenty-odd places to forget; the router keeps that knowledge in
one place.

Two rules it must hold, and both are load-bearing:

* **A chat resumes through the CLI that created it.** The backend comes from
  the record, not from current config — an operator who switches backends
  still has their older conversations, and resuming one through the wrong CLI
  would start a blank conversation that merely looks resumed.
* **Records are never routed.** They are backend-agnostic and every manager
  reads the same directory; if each backend kept its own view, the chat list
  the UI shows as one would fragment.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_core_lib.data_layers.service.agent_session_router import (
    AgentSessionRouter,
)


def _record(backend=''):
    return SimpleNamespace(task_id='PROJ-1', agent_backend=backend)


class _Harness(unittest.TestCase):
    def setUp(self) -> None:
        self.claude = MagicMock(name='claude-manager')
        self.codex = MagicMock(name='codex-manager')
        self.records = self.claude  # claude owns record bookkeeping
        self.router = AgentSessionRouter(
            managers={'claude': self.claude, 'codex': self.codex},
            record_manager=self.records,
            default_backend='claude',
            logger=MagicMock(),
        )

    def _record_says(self, backend):
        self.records.get_record.return_value = _record(backend)


class BackendResolutionTests(_Harness):
    def test_the_backend_comes_from_the_record(self) -> None:
        self._record_says('codex')
        self.assertEqual(self.router.backend_for('PROJ-1'), 'codex')

    def test_a_chat_recorded_before_backends_were_tracked_uses_the_default(self) -> None:
        # These records exist on every operator's disk; they were all created
        # by the backend that was configured then, which is the default.
        self._record_says('')
        self.assertEqual(self.router.backend_for('PROJ-1'), 'claude')

    def test_no_record_at_all_uses_the_default(self) -> None:
        self.records.get_record.return_value = None
        self.assertEqual(self.router.backend_for('PROJ-1'), 'claude')

    def test_casing_and_padding_in_a_record_do_not_break_routing(self) -> None:
        self._record_says('  CODEX ')
        self.assertIs(self.router.manager_for('PROJ-1'), self.codex)


class LiveSessionRoutingTests(_Harness):
    def test_a_codex_chat_is_fetched_from_the_codex_manager(self) -> None:
        self._record_says('codex')

        self.router.get_session('PROJ-1')

        self.codex.get_session.assert_called_once_with('PROJ-1')
        self.claude.get_session.assert_not_called()

    def test_a_claude_chat_is_fetched_from_the_claude_manager(self) -> None:
        self._record_says('claude')

        self.router.get_session('PROJ-1')

        self.claude.get_session.assert_called_once_with('PROJ-1')
        self.codex.get_session.assert_not_called()

    def test_terminate_reaches_the_owning_backend(self) -> None:
        self._record_says('codex')

        self.router.terminate_session('PROJ-1', remove_record=True)

        self.codex.terminate_session.assert_called_once_with(
            'PROJ-1', remove_record=True,
        )

    def test_an_explicit_backend_wins_when_STARTING_a_chat(self) -> None:
        # This is how a NEW chat on another backend gets created at all: the
        # record does not exist yet, or still names the previous backend.
        self._record_says('claude')

        self.router.start_session(task_id='PROJ-1', agent_backend='codex')

        self.codex.start_session.assert_called_once()
        self.claude.start_session.assert_not_called()

    def test_the_explicit_backend_is_not_forwarded_to_the_manager(self) -> None:
        # Managers take their own kwargs; passing a routing hint through would
        # be a TypeError at spawn time.
        self._record_says('claude')

        self.router.start_session(task_id='PROJ-1', agent_backend='codex')

        _, kwargs = self.codex.start_session.call_args
        self.assertNotIn('agent_backend', kwargs)

    def test_starting_without_a_choice_follows_the_record(self) -> None:
        self._record_says('codex')

        self.router.start_session(task_id='PROJ-1')

        self.codex.start_session.assert_called_once()


class UnwiredBackendTests(_Harness):
    def test_a_backend_this_host_cannot_run_falls_back_and_says_so(self) -> None:
        # The operator switched configuration, or a backend was removed. An
        # unannounced fallback would answer with the WRONG CLI silently.
        self._record_says('openhands')

        manager = self.router.manager_for('PROJ-1')

        self.assertIs(manager, self.claude)
        self.router.logger.warning.assert_called_once()

    def test_the_default_backend_never_warns(self) -> None:
        self._record_says('claude')

        self.router.manager_for('PROJ-1')

        self.router.logger.warning.assert_not_called()


class RecordsAreNotRoutedTests(_Harness):
    def test_records_always_come_from_the_record_owner(self) -> None:
        self._record_says('codex')

        self.router.get_record('PROJ-1')
        self.router.list_records()

        self.codex.get_record.assert_not_called()
        self.codex.list_records.assert_not_called()
        self.records.list_records.assert_called_once()

    def test_status_updates_go_to_the_record_owner(self) -> None:
        self._record_says('codex')

        self.router.update_status('PROJ-1', 'done')

        self.records.update_status.assert_called_once_with('PROJ-1', 'done')
        self.codex.update_status.assert_not_called()


class BroadcastTests(_Harness):
    def test_shutdown_reaches_every_backend(self) -> None:
        self.router.shutdown()

        self.claude.shutdown.assert_called_once()
        self.codex.shutdown.assert_called_once()

    def test_one_failing_shutdown_does_not_strand_the_others(self) -> None:
        self.claude.shutdown.side_effect = RuntimeError('stuck')

        self.router.shutdown()

        self.codex.shutdown.assert_called_once()

    def test_the_workspace_manager_is_attached_to_every_backend(self) -> None:
        workspace = MagicMock()

        self.router.attach_workspace_manager(workspace)

        self.claude.attach_workspace_manager.assert_called_once_with(workspace)
        self.codex.attach_workspace_manager.assert_called_once_with(workspace)

    def test_a_manager_wired_twice_is_only_called_once(self) -> None:
        # The record owner is usually ALSO one of the routed managers.
        self.router.shutdown()
        self.assertEqual(self.claude.shutdown.call_count, 1)


if __name__ == '__main__':
    unittest.main()
