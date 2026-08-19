"""The per-task mode lock must hold on EVERY spawn, not just the chat route.

An operator set a task to Plan/Explain and the agent kept editing files.
The lock was real — it just lived only in the webserver's in-memory
override map, so exactly one spawn site read it. Autonomous
implementation, review-comment fixes and diff-comment respawns all
started with the configured editing mode, and the next 180s scan tick
would pick the task up and edit.

No test caught it because the one path that DID honour the lock was the
one under test.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from kato_core_lib.data_layers.service.planning_session_runner import (
    PlanningSessionRunner,
    StreamingSessionDefaults,
    _task_mode_spawn,
)
from kato_core_lib.helpers.explain_mode_utils import EXPLAIN_MODE


class _LockFileMixin:
    """Point the store at a throwaway file — never the operator's real one."""

    def setUp(self) -> None:
        super().setUp()
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        path = os.path.join(self._dir.name, 'plan_mode.json')
        patcher = mock.patch.dict(os.environ, {'KATO_PLAN_MODE_PATH': path})
        patcher.start()
        self.addCleanup(patcher.stop)
        self._path = path

    def _lock(self, task_id: str, mode: str) -> None:
        with open(self._path, 'w', encoding='utf-8') as handle:
            json.dump({task_id: mode}, handle)


class TaskModeSpawnTests(_LockFileMixin, unittest.TestCase):
    def test_no_lock_yields_no_override(self) -> None:
        self.assertEqual(_task_mode_spawn('PROJ-1'), {})

    def test_plan_lock_becomes_a_permission_mode(self) -> None:
        self._lock('PROJ-1', 'plan')
        self.assertEqual(_task_mode_spawn('PROJ-1')['permission_mode'], 'plan')

    def test_explain_lock_expands_to_a_read_only_tool_set(self) -> None:
        # Explain is NOT a raw --permission-mode. Passing 'explain' through
        # would fail the spawn; dropping the tool set would leave a session
        # the operator believes is read-only able to edit.
        self._lock('PROJ-1', EXPLAIN_MODE)
        spawn = _task_mode_spawn('PROJ-1')
        self.assertNotEqual(spawn['permission_mode'], EXPLAIN_MODE)
        self.assertTrue(spawn['disallowed_tools'])
        self.assertIn('Edit', spawn['disallowed_tools'])
        self.assertIn('Write', spawn['disallowed_tools'])

    def test_a_blank_task_id_is_not_a_lookup(self) -> None:
        self.assertEqual(_task_mode_spawn(''), {})

    def test_an_unreadable_lock_file_never_blocks_a_spawn(self) -> None:
        with open(self._path, 'w', encoding='utf-8') as handle:
            handle.write('{not json')
        self.assertEqual(_task_mode_spawn('PROJ-1'), {})


class EverySpawnHonoursTheLockTests(_LockFileMixin, unittest.TestCase):
    """``_start_session`` is the single funnel — assert AT it."""

    def _runner(self):
        manager = mock.MagicMock()
        runner = PlanningSessionRunner(
            session_manager=manager,
            defaults=StreamingSessionDefaults(permission_mode='acceptEdits'),
        )
        return runner, manager

    def _spawn_kwargs(self, manager):
        self.assertTrue(manager.start_session.called)
        return manager.start_session.call_args.kwargs

    def test_an_unlocked_task_uses_the_configured_default(self) -> None:
        runner, manager = self._runner()
        runner._start_session(
            task_id='PROJ-1', task_summary='s', initial_prompt='p', cwd='/w',
        )
        self.assertEqual(self._spawn_kwargs(manager)['permission_mode'], 'acceptEdits')

    def test_a_plan_locked_task_spawns_in_plan_mode(self) -> None:
        # This is the autonomous path: no caller passes permission_mode, so
        # before the fix it spawned at acceptEdits and edited files.
        self._lock('PROJ-1', 'plan')
        runner, manager = self._runner()
        runner._start_session(
            task_id='PROJ-1', task_summary='s', initial_prompt='p', cwd='/w',
        )
        self.assertEqual(self._spawn_kwargs(manager)['permission_mode'], 'plan')

    def test_an_explain_locked_task_spawns_without_edit_tools(self) -> None:
        self._lock('PROJ-1', EXPLAIN_MODE)
        runner, manager = self._runner()
        runner._start_session(
            task_id='PROJ-1', task_summary='s', initial_prompt='p', cwd='/w',
        )
        kwargs = self._spawn_kwargs(manager)
        self.assertIn('Edit', kwargs['disallowed_tools'])
        self.assertIn('Write', kwargs['disallowed_tools'])

    def test_the_lock_only_applies_to_its_own_task(self) -> None:
        self._lock('PROJ-1', 'plan')
        runner, manager = self._runner()
        runner._start_session(
            task_id='OTHER-9', task_summary='s', initial_prompt='p', cwd='/w',
        )
        self.assertEqual(self._spawn_kwargs(manager)['permission_mode'], 'acceptEdits')

    def test_an_explicit_caller_argument_still_wins(self) -> None:
        # The chat route resolves the same lock itself and threads Explain's
        # tool set through; it must not be second-guessed here.
        self._lock('PROJ-1', 'plan')
        runner, manager = self._runner()
        runner._start_session(
            task_id='PROJ-1', task_summary='s', initial_prompt='p', cwd='/w',
            permission_mode='bypassPermissions',
        )
        self.assertEqual(
            self._spawn_kwargs(manager)['permission_mode'], 'bypassPermissions',
        )


if __name__ == '__main__':
    unittest.main()
