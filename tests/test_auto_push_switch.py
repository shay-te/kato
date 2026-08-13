"""Coverage for ``KATO_AUTO_PUSH_ENABLED`` — the gate that stops the
autonomous scan flow pushing a branch and opening a pull request on its own.

kato's standing policy is that publishing is an operator action. The flow did
not enforce it: the pause was opt-IN, keyed on the per-task
``kato:wait-before-git-push`` tag, so every untagged task ran straight through
``publish_task_execution`` — push, PR, summary comment, move to "In Review".
This pins the inverted default and the two ways to override it.

Two halves, mirroring ``test_review_comment_switch``:

* the resolver (``push_approval_gate_utils``) — precedence + the default-OFF
  contract, and
* the enforcement in :class:`AgentService` — the flow parks instead of
  publishing, the tag still forces a park when the switch is on, and the
  operator's own ``approve_push`` is never gated.

The settings file is redirected to a tmpfile per-test so nothing reads the
operator's real ``~/.kato/settings.json``.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kato_core_lib.data_layers.data.fields import TaskTags
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.agent_service import AgentService
from kato_core_lib.helpers.kato_settings_schema_utils import (
    DEFAULT_ON_BOOL_KEYS,
    all_settings_keys,
)
from kato_core_lib.helpers.push_approval_gate_utils import (
    AUTO_PUSH_ENABLED_KEY,
    auto_push_enabled,
)
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext


def _kwargs(**overrides):
    """Minimum valid kwargs for AgentService(...) — mirrors the main suite."""
    defaults = dict(
        task_service=MagicMock(),
        task_state_service=MagicMock(),
        implementation_service=MagicMock(),
        testing_service=MagicMock(),
        repository_service=MagicMock(),
        notification_service=MagicMock(),
    )
    defaults.update(overrides)
    return defaults


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
        os.environ.pop(AUTO_PUSH_ENABLED_KEY, None)

    def _write_settings(self, value: str) -> None:
        self.settings_path.write_text(
            json.dumps({AUTO_PUSH_ENABLED_KEY: value}), encoding='utf-8',
        )


class AutoPushGateResolverTests(_SettingsFileBase):

    def test_unset_means_disabled(self) -> None:
        # The switch is an opt-IN. Pushing a branch and opening a PR is
        # outward-facing and awkward to walk back, so an operator who never
        # touches the switch gets the safe behaviour, not the autonomous one.
        self.assertFalse(auto_push_enabled(env={}))

    def test_settings_file_true_enables(self) -> None:
        self._write_settings('true')
        self.assertTrue(auto_push_enabled(env={}))

    def test_settings_file_false_disables(self) -> None:
        self._write_settings('false')
        self.assertFalse(auto_push_enabled(env={}))

    def test_alternative_truthy_spellings(self) -> None:
        for spelling in ('true', 'TRUE', ' True ', '1', 'yes', 'on'):
            self._write_settings(spelling)
            self.assertTrue(
                auto_push_enabled(env={}),
                f'{spelling!r} should read as on',
            )

    def test_anything_unrecognised_stays_off(self) -> None:
        # Fail SAFE: a typo'd value must not be read as permission to publish.
        for spelling in ('maybe', 'yes please', '2', ''):
            self._write_settings(spelling)
            self.assertFalse(
                auto_push_enabled(env={}),
                f'{spelling!r} should read as off',
            )

    def test_settings_file_beats_shell_env(self) -> None:
        # settings.json is the live, UI-managed source; env is the fallback
        # for a key only ever exported in a shell.
        self._write_settings('false')
        self.assertFalse(auto_push_enabled(env={AUTO_PUSH_ENABLED_KEY: 'true'}))

    def test_shell_env_used_when_settings_file_silent(self) -> None:
        self.assertTrue(auto_push_enabled(env={AUTO_PUSH_ENABLED_KEY: 'true'}))

    def test_corrupt_settings_file_falls_through_to_default(self) -> None:
        self.settings_path.write_text('{not json', encoding='utf-8')
        self.assertFalse(auto_push_enabled(env={}))

    def test_key_is_offered_in_the_settings_ui(self) -> None:
        self.assertIn(AUTO_PUSH_ENABLED_KEY, all_settings_keys())

    def test_key_is_not_registered_as_default_on(self) -> None:
        # It defaults OFF, so the UI toggle must draw unchecked — listing it
        # here would render an on-looking switch over off behaviour.
        self.assertNotIn(AUTO_PUSH_ENABLED_KEY, DEFAULT_ON_BOOL_KEYS)


class AutoPushEnforcementTests(_SettingsFileBase):
    """The decision the scan loop makes once implementation + testing pass."""

    def _service(self):
        publisher = MagicMock()
        publisher.publish_task_execution.return_value = {'status': 'published'}
        service = AgentService(**_kwargs(
            task_publisher=publisher,
            workspace_manager=MagicMock(),
        ))
        return service, publisher

    def _prepared(self):
        return PreparedTaskContext(
            repositories=[], repository_branches={}, branch_name='b',
        )

    def _finish(self, service, task):
        """Run the publish decision the way ``process_assigned_task`` does.

        Calls the SAME predicate the flow calls rather than restating its
        condition — a copy here would keep passing after the real branch
        drifted, which is precisely how the gate went missing before.
        """
        prepared, execution = self._prepared(), {'success': True}
        if service._should_pause_for_push_approval(task):
            return service._pause_for_push_approval(task, prepared, execution)
        return service._task_publisher.publish_task_execution(
            task, prepared, execution,
        )

    def test_untagged_task_parks_instead_of_publishing(self) -> None:
        # The regression this whole switch exists for: no tag, no operator,
        # and kato used to push + open a PR anyway.
        service, publisher = self._service()
        result = self._finish(service, Task(id='T1'))

        self.assertEqual(result['status'], 'awaiting_push_approval')
        publisher.publish_task_execution.assert_not_called()
        self.assertTrue(service.is_awaiting_push_approval('T1'))

    def test_switch_on_publishes_an_untagged_task(self) -> None:
        self._write_settings('true')
        service, publisher = self._service()
        result = self._finish(service, Task(id='T1'))

        self.assertEqual(result, {'status': 'published'})
        publisher.publish_task_execution.assert_called_once()

    def test_tag_still_parks_even_with_the_switch_on(self) -> None:
        # The per-task tag is a stricter statement than the global switch, so
        # turning autonomy on must not override it.
        self._write_settings('true')
        service, publisher = self._service()
        result = self._finish(
            service, Task(id='T1', tags=[TaskTags.WAIT_BEFORE_GIT_PUSH]),
        )

        self.assertEqual(result['status'], 'awaiting_push_approval')
        publisher.publish_task_execution.assert_not_called()

    def test_parked_task_publishes_once_the_operator_approves(self) -> None:
        # The gate delays publishing; it must not break it.
        service, publisher = self._service()
        self._finish(service, Task(id='T1'))

        self.assertEqual(service.approve_push('T1'), {'status': 'published'})
        publisher.publish_task_execution.assert_called_once()
        self.assertFalse(service.is_awaiting_push_approval('T1'))

    def test_park_comment_names_the_switch_not_the_tag(self) -> None:
        # The comment used to hard-code the tag as the reason, which is now
        # wrong for most parked tasks — "why didn't kato open my PR?" has to
        # answer itself on the ticket.
        task_service = MagicMock()
        service = AgentService(**_kwargs(
            task_service=task_service, workspace_manager=MagicMock(),
        ))
        service._pause_for_push_approval(
            Task(id='T1'), self._prepared(), {'success': True},
        )

        body = task_service.add_comment.call_args[0][1]
        self.assertIn(AUTO_PUSH_ENABLED_KEY, body)
        self.assertNotIn(TaskTags.WAIT_BEFORE_GIT_PUSH, body)

    def test_park_comment_still_names_the_tag_when_that_is_the_reason(self) -> None:
        task_service = MagicMock()
        service = AgentService(**_kwargs(
            task_service=task_service, workspace_manager=MagicMock(),
        ))
        service._pause_for_push_approval(
            Task(id='T1', tags=[TaskTags.WAIT_BEFORE_GIT_PUSH]),
            self._prepared(), {'success': True},
        )

        body = task_service.add_comment.call_args[0][1]
        self.assertIn(TaskTags.WAIT_BEFORE_GIT_PUSH, body)


if __name__ == '__main__':
    unittest.main()
