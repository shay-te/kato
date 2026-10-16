"""A ``kato:model:<family>[:<version>]`` tag selects the model a task's chat runs on.

``kato:model:sonnet`` means the latest sonnet (the CLI alias resolves it);
``kato:model:sonnet:5.5`` pins that release. The tag is read off the ticket
whenever kato reads the task, recorded per task, and applied by the spawn funnel
itself — because a chat is spawned long after the ticket was last read (the next
message after the session went idle, a comment run, a restart).

It is the task's STARTING model: the operator's own pick in the chat outranks
it, as it outranks the configured default. And it names a Claude model, so it
never reaches a chat that is on another agent.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from kato_core_lib.data_layers.service.planning_session_runner import (
    PlanningSessionRunner,
    StreamingSessionDefaults,
)
from kato_core_lib.data_layers.service.wait_planning_service import WaitPlanningService
from kato_core_lib.helpers.task_model_tag_store import set_task_model_tag, task_model_tag
from tests.utils import build_task


class _TagFileMixin:
    """Throwaway state files — never the operator's, never another test's."""

    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.tags_path = root / 'task_model_tags.json'
        patcher = patch.dict(os.environ, {
            'KATO_TASK_MODEL_TAGS_PATH': str(self.tags_path),
            'KATO_PLANNING_HOLD_PATH': str(root / 'planning_holds.json'),
            'KATO_PLAN_MODE_PATH': str(root / 'plan_mode.json'),
        })
        patcher.start()
        self.addCleanup(patcher.stop)


class TaskModelTagStoreTests(_TagFileMixin, unittest.TestCase):
    def test_a_task_has_no_model_by_default(self) -> None:
        self.assertEqual(task_model_tag('UNA-1'), '')

    def test_a_model_is_recorded_once_and_reports_the_change(self) -> None:
        self.assertTrue(set_task_model_tag('UNA-1', 'sonnet'))
        self.assertEqual(task_model_tag('UNA-1'), 'sonnet')
        self.assertFalse(set_task_model_tag('UNA-1', 'sonnet'))

    def test_a_changed_model_replaces_the_old_one(self) -> None:
        set_task_model_tag('UNA-1', 'sonnet')
        self.assertTrue(set_task_model_tag('UNA-1', 'claude-opus-4-8'))
        self.assertEqual(task_model_tag('UNA-1'), 'claude-opus-4-8')

    def test_an_empty_model_releases_the_task(self) -> None:
        set_task_model_tag('UNA-1', 'sonnet')
        self.assertTrue(set_task_model_tag('UNA-1', ''))
        self.assertEqual(task_model_tag('UNA-1'), '')
        self.assertFalse(set_task_model_tag('UNA-1', ''))

    def test_ids_match_whatever_their_case_and_keep_one_entry(self) -> None:
        set_task_model_tag('UNA-1', 'sonnet')
        self.assertEqual(task_model_tag('una-1'), 'sonnet')
        self.assertFalse(set_task_model_tag('una-1', 'sonnet'))
        self.assertTrue(set_task_model_tag('una-1', 'opus'))
        self.assertEqual(json.loads(self.tags_path.read_text()), {'UNA-1': 'opus'})

    def test_models_are_on_disk_so_they_survive_a_restart(self) -> None:
        set_task_model_tag('UNA-2', 'haiku')
        set_task_model_tag('UNA-1', 'opus')
        self.assertEqual(
            json.loads(self.tags_path.read_text()), {'UNA-1': 'opus', 'UNA-2': 'haiku'},
        )

    def test_one_tasks_model_does_not_leak_to_another(self) -> None:
        set_task_model_tag('UNA-1', 'opus')
        self.assertEqual(task_model_tag('UNA-10'), '')

    def test_a_corrupt_or_foreign_file_selects_nothing_and_is_rewritten_cleanly(self) -> None:
        self.tags_path.write_text('{not json')
        self.assertEqual(task_model_tag('UNA-1'), '')
        self.assertTrue(set_task_model_tag('UNA-1', 'opus'))
        self.assertEqual(json.loads(self.tags_path.read_text()), {'UNA-1': 'opus'})
        self.tags_path.write_text(json.dumps(['not', 'a', 'map']))
        self.assertEqual(task_model_tag('UNA-1'), '')

    def test_a_blank_id_is_never_recorded(self) -> None:
        self.assertFalse(set_task_model_tag('  ', 'opus'))
        self.assertEqual(task_model_tag(''), '')
        self.assertFalse(self.tags_path.exists())


class ObservingTheTagTests(_TagFileMixin, unittest.TestCase):
    def _service(self, *, track=True):
        service = WaitPlanningService(
            session_manager=MagicMock(),
            repository_service=Mock(**{'resolve_task_repositories.return_value': []}),
            task_state_service=Mock(),
            planning_session_runner=MagicMock(),
            track_planning_holds=track,
            logger=MagicMock(),
        )
        return service

    def test_the_tag_on_the_ticket_selects_the_model(self) -> None:
        service = self._service()
        service.observe_model_tag(build_task(task_id='UNA-1', tags=['kato:model:sonnet:5.5']))
        self.assertEqual(task_model_tag('UNA-1'), 'claude-sonnet-5-5')

    def test_a_tag_without_a_version_selects_the_alias_so_the_latest_runs(self) -> None:
        service = self._service()
        service.observe_model_tag(build_task(task_id='UNA-1', tags=['kato:model:sonnet']))
        self.assertEqual(task_model_tag('UNA-1'), 'sonnet')

    def test_removing_the_tag_releases_the_model_on_the_next_sighting(self) -> None:
        service = self._service()
        service.observe_model_tag(build_task(task_id='UNA-1', tags=['kato:model:opus']))
        service.observe_model_tag(build_task(task_id='UNA-1', tags=['something-else']))
        self.assertEqual(task_model_tag('UNA-1'), '')

    def test_it_records_nothing_unless_the_app_turned_tracking_on(self) -> None:
        service = self._service(track=False)
        service.observe_model_tag(build_task(task_id='UNA-1', tags=['kato:model:opus']))
        self.assertEqual(task_model_tag('UNA-1'), '')
        self.assertFalse(self.tags_path.exists())

    def test_the_first_valid_tag_wins_and_unrelated_tags_are_ignored(self) -> None:
        service = self._service()
        service.observe_model_tag(build_task(task_id='UNA-1', tags=[
            'kato:repo:alpha', 'kato:model:haiku', 'kato:model:opus',
        ]))
        self.assertEqual(task_model_tag('UNA-1'), 'haiku')

    def test_a_malformed_tag_is_reported_once_not_on_every_scan(self) -> None:
        service = self._service()
        task = build_task(task_id='UNA-1', tags=['kato:model:gpt:5'])
        service.observe_model_tag(task)
        service.observe_model_tag(task)
        service.observe_model_tag(task)
        self.assertEqual(task_model_tag('UNA-1'), '')
        self.assertEqual(service.logger.warning.call_count, 1)
        self.assertIn('kato:model:sonnet:5.5', service.logger.warning.call_args.args[0])

    def test_a_malformed_tag_does_not_block_a_valid_one(self) -> None:
        service = self._service()
        service.observe_model_tag(build_task(task_id='UNA-1', tags=[
            'kato:model:nonsense', 'kato:model:opus',
        ]))
        self.assertEqual(task_model_tag('UNA-1'), 'opus')

    def test_a_failure_to_record_is_logged_and_never_raised(self) -> None:
        service = self._service()
        with patch(
            'kato_core_lib.data_layers.service.wait_planning_service.set_task_model_tag',
            side_effect=OSError('disk full'),
        ):
            service.observe_model_tag(build_task(task_id='UNA-1', tags=['kato:model:opus']))
        service.logger.exception.assert_called_once()

    def test_the_tag_is_recorded_when_a_task_is_handled_even_without_a_hold_tag(self) -> None:
        service = self._service()
        outcome = service.handle_task(build_task(task_id='UNA-1', tags=['kato:model:opus']))
        # No hold tag, so the autonomous flow carries on — but the model was seen.
        self.assertIsNone(outcome)
        self.assertEqual(task_model_tag('UNA-1'), 'opus')

    def test_the_started_task_sweep_picks_up_a_tag_added_mid_task(self) -> None:
        service = self._service()
        service.sync_planning_holds([
            build_task(task_id='UNA-1', tags=['kato:model:sonnet']),
            build_task(task_id='UNA-2', tags=[]),
        ])
        self.assertEqual(task_model_tag('UNA-1'), 'sonnet')
        self.assertEqual(task_model_tag('UNA-2'), '')


class EverySpawnUsesTheTaggedModelTests(_TagFileMixin, unittest.TestCase):
    """Asserted at ``start_session`` — the funnel every spawn path goes through."""

    def _spawned_model(self, *, backend='claude', binary='claude', configured='opus', **kwargs):
        manager = MagicMock()
        manager.backend_for = (lambda task_id: backend) if backend is not None else None
        runner = PlanningSessionRunner(
            session_manager=manager,
            defaults=StreamingSessionDefaults(binary=binary, model=configured),
        )
        runner.start_session(
            task_id='UNA-1', task_summary='s', initial_prompt='p', cwd='/w', **kwargs,
        )
        return manager.start_session.call_args.kwargs['model']

    def test_an_untagged_task_keeps_the_configured_model(self) -> None:
        self.assertEqual(self._spawned_model(), 'opus')

    def test_a_tagged_task_spawns_on_the_tagged_model(self) -> None:
        set_task_model_tag('UNA-1', 'claude-sonnet-5-5')
        self.assertEqual(self._spawned_model(), 'claude-sonnet-5-5')

    def test_the_operators_own_pick_outranks_the_tag(self) -> None:
        set_task_model_tag('UNA-1', 'sonnet')
        self.assertEqual(self._spawned_model(model='haiku'), 'haiku')

    def test_releasing_the_tag_gives_the_configured_model_back(self) -> None:
        set_task_model_tag('UNA-1', 'sonnet')
        set_task_model_tag('UNA-1', '')
        self.assertEqual(self._spawned_model(), 'opus')

    def test_a_chat_on_another_agent_is_never_handed_a_claude_model(self) -> None:
        set_task_model_tag('UNA-1', 'sonnet')
        self.assertEqual(
            self._spawned_model(backend='codex', binary='codex', configured='gpt-5'), 'gpt-5',
        )

    def test_with_no_backend_answer_the_configured_binary_decides(self) -> None:
        set_task_model_tag('UNA-1', 'sonnet')
        # A manager that cannot say which agent a task is on: the configured
        # binary does. Claude's (even as a full Windows path) takes the tag...
        self.assertEqual(self._spawned_model(backend='', binary=r'C:\bin\claude.exe'), 'sonnet')
        # ...another agent's does not.
        self.assertEqual(
            self._spawned_model(backend='', binary='/usr/bin/codex', configured='gpt-5'), 'gpt-5',
        )

    def test_a_manager_without_a_resolver_falls_back_to_the_binary(self) -> None:
        set_task_model_tag('UNA-1', 'sonnet')
        manager = SimpleNamespace(start_session=MagicMock())
        runner = PlanningSessionRunner(
            session_manager=manager, defaults=StreamingSessionDefaults(binary='claude'),
        )
        self.assertEqual(runner.tagged_model('UNA-1'), 'sonnet')

    def test_a_resolver_that_raises_falls_back_to_the_binary(self) -> None:
        set_task_model_tag('UNA-1', 'sonnet')
        manager = MagicMock()
        manager.backend_for.side_effect = RuntimeError('index locked')
        runner = PlanningSessionRunner(
            session_manager=manager, defaults=StreamingSessionDefaults(binary='claude'),
        )
        self.assertEqual(runner.tagged_model('UNA-1'), 'sonnet')

    def test_a_chat_respawn_after_a_restart_uses_the_tag_too(self) -> None:
        # The path that has no ticket in hand: the operator's next message.
        set_task_model_tag('UNA-1', 'claude-sonnet-5-5')
        manager = MagicMock()
        manager.backend_for = lambda task_id: 'claude'
        runner = PlanningSessionRunner(
            session_manager=manager, defaults=StreamingSessionDefaults(model='opus'),
        )
        runner.resume_session_for_chat(task_id='UNA-1', message='continue', cwd='/w')
        self.assertEqual(
            manager.start_session.call_args.kwargs['model'], 'claude-sonnet-5-5',
        )


if __name__ == '__main__':
    unittest.main()
