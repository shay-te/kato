"""The review loop inside kato: built by AgentService, guarded, closed at boot.

Real objects throughout: AgentService with a real implementation service (on
the stand-in ``claude`` executable), a real workspace service and session
manager, real comment stores, real loop stores. Loops are held running the
honest way — waiting behind a real queued diff comment.
"""
from __future__ import annotations

import inspect
import logging
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
from review_loop_core_lib.review_loop_core_lib.data.state import (
    ReviewLoopState,
    ReviewLoopStatus,
)
from review_loop_core_lib.review_loop_core_lib.ports import RepoDiff
from review_loop_core_lib.review_loop_core_lib.service import ReviewLoopService
from review_loop_core_lib.review_loop_core_lib.store import ReviewLoopStore
from workspace_core_lib.workspace_core_lib import WorkspaceCoreLib

from kato_core_lib import main as main_module
from kato_core_lib.comment_core_lib import CommentRecord, KatoCommentStatus
from kato_core_lib.data_layers.service.agent_service import AgentService
from kato_core_lib.data_layers.service.implementation_service import (
    ImplementationService,
)
from kato_core_lib.data_layers.service.review_loop_adapters import (
    REVIEW_LOOPS_DIR_ENV,
)
from kato_core_lib.data_layers.service.task_comment_run_service import (
    TaskCommentRunService,
)
from kato_core_lib.data_layers.service.testing_service import TestingService
from kato_core_lib.helpers.comment_store_utils import comment_store_for
from tests.fake_claude_cli import FakeClaudeCli


def _wait(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class _Kato(object):
    """AgentService over a real workspace with one real repo clone."""

    def __init__(self, test: unittest.TestCase) -> None:
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name))
        env = patch.dict(os.environ, {
            'KATO_WORKSPACES_ROOT': str(self.root / 'ws'),
            'AGENT_WORKSPACES_ROOT': str(self.root / 'ws'),
            REVIEW_LOOPS_DIR_ENV: str(self.root / 'loops'),
        })
        env.start()
        test.addCleanup(env.stop)
        cli = FakeClaudeCli(self.root / 'cli')
        self.workspaces = WorkspaceCoreLib(
            root=str(self.root / 'ws'), max_parallel_tasks=1,
            metadata_filename='.kato-meta.json', preflight_log_filename='.kato-preflight.log',
        ).workspaces
        self.workspaces.create(task_id='T-1', task_summary='S', repository_ids=['api'])
        (self.root / 'ws' / 'T-1' / 'api').mkdir()
        self.sessions = ClaudeSessionManager(state_dir=str(self.root / 'sessions'))
        test.addCleanup(self.sessions.shutdown)
        self.service = AgentService(
            # Unrelated to the review loop and never called by these tests.
            task_service=object(), task_state_service=object(),
            repository_service=object(), notification_service=object(),
            implementation_service=ImplementationService(ClaudeCliClient(binary=cli.binary)),
            testing_service=TestingService(ClaudeCliClient(binary=cli.binary)),
            session_manager=self.sessions,
            workspace_manager=self.workspaces,
            logger=logging.getLogger('test.review_loop.wiring'),
        )
        test.addCleanup(self.service.review_loops.shutdown, 'test over')

    def hold_a_loop_running(self) -> None:
        """Start a loop that waits — truthfully — behind a queued diff comment."""
        store = comment_store_for(self.workspaces, 'T-1')
        comment = store.add(CommentRecord(repo_id='api', file_path='a.py', line=1, body='x'))
        store.update_kato_status(comment.id, kato_status=KatoCommentStatus.QUEUED.value)
        diffs = lambda task_id: [RepoDiff(repo_id='api', diff='diff --git a/a b/a\n+1\n')]  # noqa: E731
        self.service.review_loops.start('T-1', diff_source=diffs)
        assert _wait(lambda: self.service.review_loops.state('T-1').waiting_for
                     == 'a diff comment goes first')


class ServiceWiringTests(unittest.TestCase):

    def test_agent_service_owns_one_review_loop_service(self) -> None:
        kato = _Kato(self)
        self.assertIsInstance(kato.service.review_loops, ReviewLoopService)
        self.assertIs(kato.service.review_loops, kato.service.review_loops)

    def test_it_sends_through_the_comment_runs_own_sender(self) -> None:
        # The same instance — so the same per-task lock as the comment drain.
        kato = _Kato(self)
        chat = kato.service.review_loops._chat
        self.assertIs(chat._delivery, kato.service.comment_runs.chat_delivery)

    def test_shutdown_interrupts_a_running_loop(self) -> None:
        kato = _Kato(self)
        kato.hold_a_loop_running()
        kato.service.shutdown()
        state = kato.service.review_loops.state('T-1')
        self.assertEqual(state.status, ReviewLoopStatus.INTERRUPTED)
        self.assertIn('shut down', state.reason)


class DoneMarkerGuardTests(unittest.TestCase):

    def test_the_marker_publishes_when_no_loop_runs(self) -> None:
        # The empty id is the one input the real publish flow answers without
        # touching git or the tracker — enough to see the call went through.
        kato = _Kato(self)
        result = kato.service.finish_from_done_marker('')
        self.assertEqual(result['error'], 'empty task id')

    def test_the_marker_is_ignored_while_the_tasks_loop_runs(self) -> None:
        # Publishing now would push a change the loop is about to review again.
        kato = _Kato(self)
        kato.hold_a_loop_running()
        with self.assertLogs('test.review_loop.wiring', level='WARNING') as logs:
            self.assertIsNone(kato.service.finish_from_done_marker('T-1'))
        self.assertIn('not publishing', logs.records[0].getMessage())
        self.assertTrue(kato.service.review_loops.is_running('T-1'))

    def test_the_done_callback_registered_at_boot_is_the_guarded_one(self) -> None:
        from kato_core_lib import kato_core_lib as kcl
        source = inspect.getsource(kcl)
        self.assertEqual(source.count('.finish_from_done_marker,'), 2)
        self.assertNotIn('publish.finish_task_planning_session,\n', source)


class BootStepTests(unittest.TestCase):

    def _loops(self) -> ReviewLoopService:
        from review_loop_core_lib.review_loop_core_lib.tests.fakes import (
            WORDING, FakeChat, FakeReviewer,
        )
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = ReviewLoopStore(Path(tmp.name))
        store.save(ReviewLoopState.new('T-9', max_rounds=5, now=1.0))
        return ReviewLoopService(store=store, chat=FakeChat(), reviewer=FakeReviewer(), wording=WORDING)

    def test_loops_left_running_are_interrupted_and_logged(self) -> None:
        loops = self._loops()
        app = SimpleNamespace(service=SimpleNamespace(review_loops=loops),
                              logger=logging.getLogger('test.boot'))
        with self.assertLogs('test.boot', level='INFO') as logs:
            main_module._mark_interrupted_review_loops(app)
        self.assertEqual(loops.state('T-9').status, ReviewLoopStatus.INTERRUPTED)
        self.assertIn('marked 1 review loop(s) interrupted', logs.records[0].getMessage())

    def test_a_second_boot_has_nothing_to_close(self) -> None:
        loops = self._loops()
        app = SimpleNamespace(service=SimpleNamespace(review_loops=loops),
                              logger=logging.getLogger('test.boot.quiet'))
        main_module._mark_interrupted_review_loops(app)
        with self.assertNoLogs('test.boot.quiet', level='INFO'):
            main_module._mark_interrupted_review_loops(app)

    def test_no_service_or_a_failing_one_never_breaks_boot(self) -> None:
        class _Unreadable(object):
            def mark_interrupted(self, reason):
                raise OSError('the loops folder is unreadable')

        main_module._mark_interrupted_review_loops(
            SimpleNamespace(service=None, logger=logging.getLogger('test.boot.none')),
        )
        app = SimpleNamespace(service=SimpleNamespace(review_loops=_Unreadable()),
                              logger=logging.getLogger('test.boot.broken'))
        with self.assertLogs('test.boot.broken', level='ERROR'):
            main_module._mark_interrupted_review_loops(app)

    def test_it_is_one_of_the_boot_reconciliation_steps(self) -> None:
        source = inspect.getsource(main_module._run_boot_reconciliation)
        self.assertIn('_mark_interrupted_review_loops(app)', source)


class PendingCommentTests(unittest.TestCase):

    def _runs(self) -> tuple[TaskCommentRunService, object]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workspaces = WorkspaceCoreLib(
            root=tmp.name, max_parallel_tasks=1,
            metadata_filename='.kato-meta.json', preflight_log_filename='.kato-preflight.log',
        ).workspaces
        workspaces.create(task_id='T-1', repository_ids=['api'])
        return TaskCommentRunService(comment_service=None, workspace_manager=workspaces), workspaces

    def test_queued_or_in_progress_is_pending_and_finished_is_not(self) -> None:
        runs, workspaces = self._runs()
        store = comment_store_for(workspaces, 'T-1')
        comment = store.add(CommentRecord(repo_id='api', file_path='a.py', line=1, body='x'))
        self.assertFalse(runs.has_local_comment_pending('T-1'))  # just added: idle
        for status, pending in ((KatoCommentStatus.QUEUED, True),
                                (KatoCommentStatus.IN_PROGRESS, True),
                                (KatoCommentStatus.ADDRESSED, False)):
            with self.subTest(status=status):
                store.update_kato_status(comment.id, kato_status=status.value)
                self.assertIs(runs.has_local_comment_pending('T-1'), pending)

    def test_a_task_without_a_workspace_has_nothing_pending(self) -> None:
        runs, _ = self._runs()
        self.assertFalse(runs.has_local_comment_pending('NO-SUCH-TASK'))

    def test_an_unreadable_comment_file_is_not_pending(self) -> None:
        runs, workspaces = self._runs()
        comments_file = Path(workspaces.workspace_path('T-1')) / '.kato-comments.json'
        comments_file.write_text('{ torn', encoding='utf-8')
        self.assertFalse(runs.has_local_comment_pending('T-1'))


if __name__ == '__main__':
    unittest.main()
