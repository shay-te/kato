"""Git actions must never wait on the agent. Reported from production.

During a Claude outage the operator found that Update Source and Push stopped
working entirely — the UI's git buttons hung with no error. The agent being
down had frozen work that has nothing to do with the agent.

The chain, all four links real:

1. an unresponsive CLI stops draining its stdin pipe, so
   ``_write_stdin_line``'s ``flush()`` blocks — holding ``_proc_lock``;
   ``terminate``'s wait-and-escalate holds the same lock just as long;
2. ``is_alive`` took ``_proc_lock``, so every liveness check blocked;
3. ``ClaudeSessionManager.get_session`` probed liveness while holding its
   GLOBAL lock, so the whole manager froze;
4. the git path asks the manager whether a live session missed a newly-synced
   repo — purely to show a "restart the tab" hint — and inherited the freeze.

These tests hold ``_proc_lock`` exactly as a wedged CLI does, then assert the
git-facing calls still return. They use real threads and real locks: a mock
lock would deadlock on nothing.
"""

from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib.data_layers.service.task_repository_service import (
    TaskRepositoryService,
)

_TIMEOUT = 5.0


def _run_with_timeout(fn):
    """Run ``fn`` on a thread; return (finished, result). Never hangs the suite."""
    box = {}

    def _target():
        try:
            box['result'] = fn()
        except Exception as exc:  # surfaced by the caller
            box['error'] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=_TIMEOUT)
    return (not thread.is_alive()), box


class _WedgedSession(object):
    """A stuck CLI: the process lock is held, liveness stays answerable.

    Mirrors the FIXED session — ``is_alive`` is lock-free, while the calls
    that legitimately take ``_proc_lock`` are the ones left stalling.
    """

    def __init__(self, lock: threading.Lock) -> None:
        self._proc_lock = lock
        self.cwd = '/wks/PROJ-1/api'
        self.is_alive = True

    def allowed_additional_dirs(self):
        with self._proc_lock:
            return []


class _UnprobeableSession(object):
    """The WORST case: even the liveness probe blocks.

    A transport that regresses ``is_alive``, or a third-party session object,
    could still stall here. What must hold regardless is that the manager does
    not hold its GLOBAL lock while waiting — otherwise one stuck session
    freezes every other caller, which is the production failure.
    """

    def __init__(self, lock: threading.Lock) -> None:
        self._proc_lock = lock
        self.cwd = '/wks/PROJ-1/api'

    @property
    def is_alive(self) -> bool:
        with self._proc_lock:
            return True


class GitActionsSurviveAWedgedAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.proc_lock = threading.Lock()
        self.session = _WedgedSession(self.proc_lock)
        self.session_manager = MagicMock()
        self.session_manager.get_session.return_value = self.session
        self.service = TaskRepositoryService(
            repository_service=MagicMock(),
            task_service=MagicMock(),
            workspace_manager=MagicMock(),
            session_manager=self.session_manager,
            logger=MagicMock(),
        )

    def test_the_restart_hint_returns_even_while_the_agent_is_wedged(self) -> None:
        provisioned = [SimpleNamespace(id='api', local_path='/wks/PROJ-1/api')]
        self.proc_lock.acquire()  # the CLI is stuck mid-write / mid-terminate
        self.addCleanup(self._release)

        finished, box = _run_with_timeout(
            lambda: self.service._sync_requires_session_restart(
                'PROJ-1', provisioned, provisioned,
            )
        )

        self.assertTrue(
            finished,
            'the restart hint blocked on a wedged agent — this is the '
            'production hang: every git action in the UI waits behind it',
        )
        self.assertNotIn('error', box)

    def _release(self) -> None:
        if self.proc_lock.locked():
            self.proc_lock.release()

    def test_a_session_lookup_that_raises_is_reported_as_no_hint(self) -> None:
        # Whatever goes wrong while asking the agent, the git work already
        # happened; the sync must report success, not fail.
        self.session_manager.get_session.side_effect = RuntimeError('agent down')
        provisioned = [SimpleNamespace(id='api', local_path='/wks/PROJ-1/api')]

        self.assertFalse(
            self.service._sync_requires_session_restart(
                'PROJ-1', provisioned, provisioned,
            )
        )


class LivenessProbeNeverBlocksTests(unittest.TestCase):
    """``is_alive`` is the link that turned one stuck CLI into a global freeze."""

    def test_is_alive_answers_while_the_process_lock_is_held(self) -> None:
        from claude_core_lib.claude_core_lib.session.streaming import (
            StreamingClaudeSession,
        )
        session = StreamingClaudeSession.__new__(StreamingClaudeSession)
        session._proc_lock = threading.Lock()
        session._proc = None
        session._proc_lock.acquire()
        self.addCleanup(session._proc_lock.release)

        finished, box = _run_with_timeout(lambda: session.is_alive)

        self.assertTrue(
            finished,
            'is_alive blocked on the process lock — a CLI stuck in flush() or '
            'terminate() freezes every liveness check in the process',
        )
        self.assertIs(box.get('result'), False)

    def test_get_session_answers_while_a_session_is_wedged(self) -> None:
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        manager = ClaudeSessionManager.__new__(ClaudeSessionManager)
        manager._lock = threading.RLock()
        manager._sessions = {}
        manager._records = {}
        lock = threading.Lock()
        manager._sessions['proj-1'] = _WedgedSession(lock)
        lock.acquire()
        self.addCleanup(lock.release)

        finished, _ = _run_with_timeout(lambda: manager.get_session('PROJ-1'))

        self.assertTrue(
            finished,
            'get_session held the manager global lock while probing a wedged '
            'session — that is what froze every other caller of the manager',
        )

    def test_the_manager_lock_is_free_while_a_session_is_wedged(self) -> None:
        # The real damage: not that one call blocked, but that it held the
        # GLOBAL lock while doing so, freezing unrelated callers.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        manager = ClaudeSessionManager.__new__(ClaudeSessionManager)
        manager._lock = threading.RLock()
        manager._sessions = {}
        manager._records = {}
        lock = threading.Lock()
        manager._sessions['proj-1'] = _UnprobeableSession(lock)
        lock.acquire()
        self.addCleanup(lock.release)

        threading.Thread(target=lambda: manager.get_session('PROJ-1'),
                         daemon=True).start()

        def _another_caller() -> bool:
            # An RLock must be released by the thread that took it, so the
            # acquire/release pair lives entirely on the probe thread.
            if not manager._lock.acquire(timeout=1.0):
                return False
            manager._lock.release()
            return True

        finished, box = _run_with_timeout(_another_caller)

        self.assertTrue(finished, 'another caller could not even TRY the lock')
        self.assertTrue(
            box.get('result'),
            'the manager global lock was still held while a session was '
            'wedged — every other manager caller, git actions included, waits',
        )


class PushDoesNotWaitOnTheTicketPlatformTests(unittest.TestCase):
    """The other way a git button froze: the pre-push tag reconcile.

    Push reconciles the ticket's ``kato:repo:`` tags into the workspace
    metadata first, so a repo tagged mid-task is not skipped. That reads the
    ticket platform — and a provider rate-limit backoff is tens of seconds.
    The button must not wait for it.
    """

    def _publish_service(self, reconcile):
        from kato_core_lib.data_layers.service.task_publish_service import (
            TaskPublishService,
        )
        return TaskPublishService(
            repository_service=MagicMock(),
            task_service=MagicMock(),
            task_state_service=MagicMock(),
            task_publisher=MagicMock(),
            workspace_manager=MagicMock(),
            reconcile_task_repositories=reconcile,
            logger=MagicMock(),
        )

    def _pushable_service(self, reconcile):
        """A publish service that reaches ``push_task``'s summary dict.

        The crash was in building that summary, so a fixture that returns
        early (no workspace context) proves nothing — it never gets there.
        """
        service = self._publish_service(reconcile)
        repository = SimpleNamespace(id='repo-a')
        service._resolve_publish_context = MagicMock(
            return_value=([repository], 'kato/PROJ-1', SimpleNamespace(id='PROJ-1')),
        )
        # Nothing to push: the per-repo work is skipped and the loop falls
        # straight through to the summary the bug lived in.
        service._repository_service.push_skip_reason.return_value = 'nothing to push'
        return service

    def test_a_hanging_provider_does_not_hold_the_push(self) -> None:
        import time
        service = self._publish_service(lambda task_id: time.sleep(60))

        # Shorten the production bound so the SUITE does not wait it out; the
        # mechanism under test is the bound existing at all.
        with patch('kato_core_lib.data_layers.service.task_publish_service'
                   '._RECONCILE_TIMEOUT_SECONDS', 0.2):
            finished, _ = _run_with_timeout(
                lambda: service._reconcile_task_repositories('PROJ-1'))

        self.assertTrue(
            finished,
            'the pre-push reconcile blocked on the ticket platform — the '
            'operator\'s Push button hangs behind it',
        )

    def test_a_fast_reconcile_still_runs_normally(self) -> None:
        seen = []
        service = self._publish_service(lambda task_id: seen.append(task_id))

        service._reconcile_task_repositories('PROJ-1')

        self.assertEqual(seen, ['PROJ-1'])

    def test_a_failing_reconcile_does_not_break_the_push(self) -> None:
        def _boom(task_id):
            raise RuntimeError('ticket platform down')

        service = self._publish_service(_boom)

        # A DICT, not None: push_task reads ``added_repositories`` off this.
        # Returning None turned a failing reconcile into an AttributeError
        # 500 on Push — the fallback taking down what it was protecting.
        self.assertEqual(service._reconcile_task_repositories('PROJ-1'), {})

    def test_a_timed_out_reconcile_returns_a_dict_not_none(self) -> None:
        def _slow(task_id):
            time.sleep(5)
            return {'added_repositories': ['late']}

        service = self._publish_service(_slow)
        with patch('kato_core_lib.data_layers.service.task_publish_service'
                   '._RECONCILE_TIMEOUT_SECONDS', 0.2):
            self.assertEqual(service._reconcile_task_repositories('PROJ-1'), {})

    def test_no_injected_reconcile_returns_a_dict_not_none(self) -> None:
        # The default stand-in used when the caller wires no reconcile at all.
        service = self._publish_service(None)
        self.assertEqual(service._reconcile_task_repositories('PROJ-1'), {})

    def test_a_successful_reconcile_is_passed_through(self) -> None:
        service = self._publish_service(
            lambda task_id: {'added_repositories': ['new-repo']},
        )
        self.assertEqual(
            service._reconcile_task_repositories('PROJ-1'),
            {'added_repositories': ['new-repo']},
        )

    def test_push_survives_a_reconcile_that_times_out(self) -> None:
        """The reported 500: Push and Update-source died on a slow provider.

        ``push_task`` reads ``added_repositories`` off the reconcile result to
        report which repos the push covered. A timed-out reconcile handed it
        ``None``, so the deadline that existed to keep the button responsive
        was instead the thing that broke it — ``AttributeError: 'NoneType'
        object has no attribute 'get'`` straight out of /update-source.
        """
        def _slow(task_id):
            time.sleep(5)

        service = self._pushable_service(_slow)
        with patch('kato_core_lib.data_layers.service.task_publish_service'
                   '._RECONCILE_TIMEOUT_SECONDS', 0.2):
            result = service.push_task('PROJ-1')

        # Reaching the summary at all is the assertion — this raised before.
        self.assertEqual(result['synced_repositories'], [])
        self.assertEqual(result['task_id'], 'PROJ-1')

    def test_push_reports_repositories_the_reconcile_pulled_in(self) -> None:
        service = self._pushable_service(
            lambda task_id: {'added_repositories': ['late-repo', '']},
        )
        result = service.push_task('PROJ-1')

        # Falsy entries dropped; the real one reaches the operator's toast.
        self.assertEqual(result['synced_repositories'], ['late-repo'])


if __name__ == '__main__':
    unittest.main()
