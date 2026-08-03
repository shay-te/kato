"""Focused coverage fillers for several kato_core_lib modules.

Each test targets a specific previously-uncovered line and asserts on
real behaviour (return values / raised errors / side effects), not just
line execution. New file so it never collides with the existing suites.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib.data_layers.data_access.lessons_data_access import (
    LessonsDataAccess,
)
from kato_core_lib.data_layers.service.comment_run_watcher import (
    CommentRunWatcher,
    build_and_start_comment_run_watcher,
)
from kato_core_lib.data_layers.service.repository_service import (
    RepositoryHasNoChangesError,
    RepositoryService,
)
from kato_core_lib.helpers import forgotten_tasks_store
from tests.utils import build_test_cfg


# --------------------------------------------------------------------------
# lessons_data_access.py:56 — the state_dir property
# --------------------------------------------------------------------------
class LessonsStateDirPropertyTests(unittest.TestCase):
    def test_state_dir_returns_the_configured_path(self) -> None:
        target = Path('/tmp/kato-lessons-test-dir')
        access = LessonsDataAccess(target)
        self.assertEqual(access.state_dir, target)
        self.assertIsInstance(access.state_dir, Path)


# --------------------------------------------------------------------------
# atomic_json_utils.py:58 — raise_on_error re-raises the OSError
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# comment_run_watcher.py:87-90 — _run_loop swallows a bad tick
# comment_run_watcher.py:134->136 — builder with autostart=False
# --------------------------------------------------------------------------
class CommentRunWatcherRunLoopTests(unittest.TestCase):
    def test_run_loop_logs_and_survives_a_failing_tick(self) -> None:
        watcher = CommentRunWatcher(service=SimpleNamespace())
        watcher.logger = MagicMock()

        # First tick raises (hits lines 87-90), then stop so the loop ends.
        def _tick():
            watcher._stop_event.set()
            raise RuntimeError('bad tick')

        with patch.object(watcher, 'tick', side_effect=_tick) as tick_mock, \
                patch.object(watcher._stop_event, 'wait') as wait_mock:
            watcher._run_loop()
        tick_mock.assert_called_once()
        watcher.logger.exception.assert_called_once_with(
            'comment-run watcher tick failed'
        )
        # The post-tick wait still ran (loop kept going past the except).
        wait_mock.assert_called_once_with(watcher._tick_seconds)

    def test_builder_without_autostart_does_not_start_thread(self) -> None:
        # autostart=False -> line 134 condition is False -> jumps to 136
        # (the 134->136 branch). No thread is started.
        watcher = build_and_start_comment_run_watcher(
            service=SimpleNamespace(),
            autostart=False,
        )
        self.assertIsInstance(watcher, CommentRunWatcher)
        self.assertIsNone(watcher._thread)

    def test_builder_with_autostart_starts_thread_then_stops(self) -> None:
        watcher = build_and_start_comment_run_watcher(
            service=SimpleNamespace(
                advance_finished_comment_runs=lambda: [],
                drain_all_queued_task_comments=lambda: [],
            ),
            tick_seconds=0.5,
            autostart=True,
        )
        try:
            self.assertIsNotNone(watcher._thread)
            self.assertTrue(watcher._thread.is_alive())
        finally:
            watcher.stop(timeout=1.0)
        self.assertIsNone(watcher._thread)


# --------------------------------------------------------------------------
# main.py:930-931 — _start_comment_run_watcher swallows a start failure
# --------------------------------------------------------------------------
class StartCommentRunWatcherFailureTests(unittest.TestCase):
    def test_logs_and_returns_when_builder_raises(self) -> None:
        from kato_core_lib import main as kato_main

        app = SimpleNamespace(service=SimpleNamespace(), logger=MagicMock())
        with patch(
            'kato_core_lib.data_layers.service.comment_run_watcher.'
            'build_and_start_comment_run_watcher',
            side_effect=RuntimeError('cannot start'),
        ):
            # Must not raise — best-effort start.
            kato_main._start_comment_run_watcher(app)
        app.logger.exception.assert_called_once()
        # No watcher was stashed on the app.
        self.assertFalse(hasattr(app, 'comment_run_watcher'))

    def test_returns_early_when_app_has_no_service(self) -> None:
        from kato_core_lib import main as kato_main

        app = SimpleNamespace(service=None, logger=MagicMock())
        kato_main._start_comment_run_watcher(app)
        app.logger.exception.assert_not_called()


# --------------------------------------------------------------------------
# repository_service.py:1045 — already-merged branch raises with that message
# repository_service.py:1059-1061 — _ensure_branch_has_task_changes
# --------------------------------------------------------------------------
def _make_repository_service() -> RepositoryService:
    return RepositoryService(build_test_cfg(), 3)


class EnsureBranchPublishableMergedTests(unittest.TestCase):
    def test_raises_already_merged_when_branch_is_behind(self) -> None:
        service = _make_repository_service()
        # ahead==0 (first _ahead_count) and behind>=1 (second, refs
        # swapped) -> the "already merged" raise on line 1045.
        with patch.object(service, '_comparison_reference', return_value='main'), \
                patch.object(service, '_ahead_count', side_effect=[0, 2]):
            with self.assertRaises(RepositoryHasNoChangesError) as ctx:
                service._ensure_branch_is_publishable('/x', 'feat/x', 'main')
        self.assertIn('already merged into main', str(ctx.exception))

    def test_raises_no_changes_when_branch_is_level(self) -> None:
        service = _make_repository_service()
        # ahead==0 and behind==0 -> the "no task changes ahead" raise.
        with patch.object(service, '_comparison_reference', return_value='main'), \
                patch.object(service, '_ahead_count', side_effect=[0, 0]):
            with self.assertRaises(RepositoryHasNoChangesError) as ctx:
                service._ensure_branch_is_publishable('/x', 'feat/x', 'main')
        self.assertIn('no task changes ahead of main', str(ctx.exception))


class EnsureBranchHasTaskChangesTests(unittest.TestCase):
    def test_returns_early_when_working_tree_dirty(self) -> None:
        service = _make_repository_service()
        # Dirty working tree -> return on line 1060, publishable check
        # never runs.
        with patch.object(service, '_working_tree_status', return_value=' M f.py'), \
                patch.object(service, '_ensure_branch_is_publishable') as pub:
            service._ensure_branch_has_task_changes('/x', 'feat/x', 'main')
        pub.assert_not_called()

    def test_delegates_to_publishable_check_when_tree_clean(self) -> None:
        service = _make_repository_service()
        # Clean working tree -> line 1061 delegates to the publishable
        # check, which here raises (clean + level branch).
        with patch.object(service, '_working_tree_status', return_value=''), \
                patch.object(service, '_ensure_branch_is_publishable') as pub:
            service._ensure_branch_has_task_changes('/x', 'feat/x', 'main')
        pub.assert_called_once_with('/x', 'feat/x', 'main')


# --------------------------------------------------------------------------
# forgotten_tasks_store.py:45 — non-list JSON yields empty set
# forgotten_tasks_store.py:70 — unforget with blank id is a no-op
# forgotten_tasks_store.py:82-83 — mkdir OSError is swallowed
# --------------------------------------------------------------------------
class ForgottenTasksStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path('/tmp/kato-forgotten-cov.json')
        if self._tmp.exists():
            self._tmp.unlink()

    def tearDown(self) -> None:
        if self._tmp.exists():
            self._tmp.unlink()

    def test_non_list_json_returns_empty_set(self) -> None:
        # A dict (not a list) on disk -> line 45 returns an empty set
        # rather than crashing.
        self._tmp.write_text(json.dumps({'unexpected': 'shape'}), encoding='utf-8')
        with patch.object(forgotten_tasks_store, '_path', return_value=self._tmp):
            self.assertEqual(forgotten_tasks_store.forgotten_task_ids(), set())

    def test_unforget_blank_id_is_noop(self) -> None:
        # Blank id -> early return on line 70; _write is never reached.
        with patch.object(forgotten_tasks_store, '_write') as write_mock:
            forgotten_tasks_store.unforget('   ')
            forgotten_tasks_store.unforget('')
        write_mock.assert_not_called()

    def test_write_swallows_mkdir_oserror_and_still_writes(self) -> None:
        # mkdir raising OSError (lines 82-83) is swallowed; the atomic
        # write still runs with the sorted ids.
        fake_path = MagicMock()
        fake_path.parent.mkdir.side_effect = OSError('readonly fs')
        with patch.object(forgotten_tasks_store, '_path', return_value=fake_path), \
                patch.object(forgotten_tasks_store, 'atomic_write_json') as write_mock:
            forgotten_tasks_store._write({'B-2', 'A-1'})
        fake_path.parent.mkdir.assert_called_once()
        write_mock.assert_called_once_with(fake_path, ['A-1', 'B-2'])


if __name__ == '__main__':
    unittest.main()
