"""Coverage for ``KATO_REVIEW_COMMENTS_ENABLED`` — the operator switch that
stops kato pulling pull-request review comments from the git host.

Two halves:

* the resolver (``review_comment_gate_utils``) — precedence + the
  default-ON contract, and
* the enforcement in :class:`ReviewCommentService` — no provider call when
  off, a queued batch dropped rather than spawned, and the in-flight run
  terminated on demand.

The settings file is redirected to a tmpfile per-test so nothing reads the
operator's real ``~/.kato/settings.json``.
"""

from __future__ import annotations

import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kato_core_lib.data_layers.data.fields import ImplementationFields
from kato_core_lib.data_layers.service.agent_state_registry import AgentStateRegistry
from kato_core_lib.data_layers.service.review_comment_service import ReviewCommentService
from kato_core_lib.helpers.kato_settings_schema_utils import (
    DEFAULT_ON_BOOL_KEYS,
    all_settings_keys,
)
from kato_core_lib.helpers.review_comment_gate_utils import (
    REVIEW_COMMENTS_ENABLED_KEY,
    review_comments_enabled,
)
from tests.utils import build_review_comment, build_task


class _SettingsFileBase(unittest.TestCase):
    """Points ``kato_settings_path()`` at a per-test tmpfile."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings_path = Path(self._tmp.name) / 'settings.json'
        patcher = patch.dict(
            os.environ, {'KATO_SETTINGS_FILE': str(self.settings_path)},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(REVIEW_COMMENTS_ENABLED_KEY, None)

    def _write_settings(self, value: str) -> None:
        self.settings_path.write_text(
            json.dumps({REVIEW_COMMENTS_ENABLED_KEY: value}), encoding='utf-8',
        )


class ReviewCommentGateResolverTests(_SettingsFileBase):

    def test_unset_means_enabled(self) -> None:
        # The switch is an opt-OUT: an operator who never touches it keeps
        # the behaviour kato has always had.
        self.assertTrue(review_comments_enabled(env={}))

    def test_settings_file_false_disables(self) -> None:
        self._write_settings('false')
        self.assertFalse(review_comments_enabled(env={}))

    def test_settings_file_true_enables(self) -> None:
        self._write_settings('true')
        self.assertTrue(review_comments_enabled(env={}))

    def test_alternative_falsy_spellings(self) -> None:
        for spelling in ('false', 'FALSE', ' False ', '0', 'no', 'off'):
            self._write_settings(spelling)
            self.assertFalse(
                review_comments_enabled(env={}),
                f'{spelling!r} should read as off',
            )

    def test_env_used_when_settings_file_has_nothing(self) -> None:
        self.assertFalse(
            review_comments_enabled(env={REVIEW_COMMENTS_ENABLED_KEY: 'false'}),
        )

    def test_settings_file_wins_over_stale_env(self) -> None:
        # Boot copies settings.json into os.environ, so the env value is a
        # SNAPSHOT. After a UI save the file is the truth — reading env
        # first would make the switch a restart-only setting.
        self._write_settings('false')
        self.assertFalse(
            review_comments_enabled(env={REVIEW_COMMENTS_ENABLED_KEY: 'true'}),
        )

    def test_corrupt_settings_file_falls_through_to_env(self) -> None:
        self.settings_path.write_text('{not json', encoding='utf-8')
        self.assertFalse(
            review_comments_enabled(env={REVIEW_COMMENTS_ENABLED_KEY: 'false'}),
        )
        self.assertTrue(review_comments_enabled(env={}))

    def test_key_is_operator_editable_and_defaults_on_in_the_ui(self) -> None:
        self.assertIn(REVIEW_COMMENTS_ENABLED_KEY, all_settings_keys())
        # Without this the toggle would draw UNCHECKED while the code
        # behind it defaults to enabled.
        self.assertIn(REVIEW_COMMENTS_ENABLED_KEY, DEFAULT_ON_BOOL_KEYS)


class ReviewCommentSwitchEnforcementTests(_SettingsFileBase):

    def setUp(self) -> None:
        super().setUp()
        self.task_service = types.SimpleNamespace(
            get_review_tasks=Mock(return_value=[build_task(task_id='PROJ-1')]),
            add_comment=Mock(),
        )
        self.repository = types.SimpleNamespace(
            id='client',
            owner='workspace',
            repo_slug='repo',
            provider_base_url='https://api.bitbucket.org/2.0',
        )
        self.repository_service = types.SimpleNamespace(
            get_repository=Mock(return_value=self.repository),
            resolve_task_repositories=Mock(return_value=[self.repository]),
            build_branch_name=Mock(return_value='PROJ-1'),
            find_pull_requests=Mock(return_value=[
                {'id': '5', 'source_branch': 'PROJ-1'},
            ]),
            list_pull_request_comments=Mock(return_value=[]),
            prepare_task_branches=Mock(),
            publish_review_fix=Mock(),
            reply_to_review_comment=Mock(),
            resolve_review_comment=Mock(),
            restore_task_repositories=Mock(),
        )
        self.implementation_service = types.SimpleNamespace(
            fix_review_comment=Mock(
                return_value={ImplementationFields.SUCCESS: True},
            ),
        )
        self.service = ReviewCommentService(
            self.task_service,
            self.implementation_service,
            self.repository_service,
            AgentStateRegistry(),
        )

    def test_poll_makes_no_git_host_call_when_off(self) -> None:
        self._write_settings('false')

        self.assertEqual(self.service.get_new_pull_request_comments(), [])
        # "Stop pulling comments" has to mean no request at all — gating
        # further down would still hit the provider on every scan tick.
        self.repository_service.list_pull_request_comments.assert_not_called()
        self.repository_service.find_pull_requests.assert_not_called()
        self.task_service.get_review_tasks.assert_not_called()

    def test_poll_runs_normally_when_on(self) -> None:
        self._write_settings('true')

        self.assertEqual(self.service.get_new_pull_request_comments(), [])
        self.task_service.get_review_tasks.assert_called_once()

    def test_queued_batch_is_dropped_without_spawning_an_agent(self) -> None:
        # The switch can flip AFTER dispatch, while the batch waits in the
        # runner's queue. It must die there, not one tick later.
        self._write_settings('false')
        comment = build_review_comment(pull_request_id='5')

        self.assertEqual(self.service.process_review_comment_batch([comment]), [])
        self.implementation_service.fix_review_comment.assert_not_called()
        self.repository_service.get_repository.assert_not_called()

    def test_no_active_runs_when_idle(self) -> None:
        self.assertEqual(self.service.active_review_comment_task_ids(), [])
        self.assertEqual(self.service.stop_active_review_comment_work(), [])

    def test_active_run_is_tracked_and_released(self) -> None:
        seen: list[list[str]] = []
        self.service._mark_review_run_active('PROJ-1')
        try:
            seen.append(self.service.active_review_comment_task_ids())
        finally:
            self.service._mark_review_run_finished('PROJ-1')
        self.assertEqual(seen, [['PROJ-1']])
        self.assertEqual(self.service.active_review_comment_task_ids(), [])

    def test_stop_terminates_the_session_of_every_active_run(self) -> None:
        manager = Mock()
        manager.get_record = Mock(return_value=object())
        self.service._planning_session_runner = types.SimpleNamespace(
            session_manager=manager,
        )
        self.service._mark_review_run_active('PROJ-1')
        self.service._mark_review_run_active('PROJ-2')

        stopped = self.service.stop_active_review_comment_work()

        self.assertEqual(stopped, ['PROJ-1', 'PROJ-2'])
        self.assertEqual(
            sorted(c.args[0] for c in manager.terminate_session.call_args_list),
            ['PROJ-1', 'PROJ-2'],
        )

    def test_stop_skips_a_task_with_no_live_session(self) -> None:
        manager = Mock()
        manager.get_record = Mock(return_value=None)
        self.service._planning_session_runner = types.SimpleNamespace(
            session_manager=manager,
        )
        self.service._mark_review_run_active('PROJ-1')

        self.assertEqual(self.service.stop_active_review_comment_work(), [])
        manager.terminate_session.assert_not_called()

    def test_stop_survives_a_failing_teardown(self) -> None:
        # One task that can't be killed must not strand the others — the
        # poll gate is already blocking new work either way.
        manager = Mock()
        manager.get_record = Mock(return_value=object())
        manager.terminate_session = Mock(
            side_effect=[RuntimeError('boom'), None],
        )
        self.service._planning_session_runner = types.SimpleNamespace(
            session_manager=manager,
        )
        self.service._mark_review_run_active('PROJ-1')
        self.service._mark_review_run_active('PROJ-2')

        self.assertEqual(self.service.stop_active_review_comment_work(), ['PROJ-2'])

    def test_stop_is_a_no_op_without_a_streaming_runner(self) -> None:
        # OpenHands / one-shot setups have no session to terminate; the
        # batch gate is what stops them, and this must not raise.
        self.service._mark_review_run_active('PROJ-1')
        self.assertEqual(self.service.stop_active_review_comment_work(), [])


if __name__ == '__main__':
    unittest.main()
