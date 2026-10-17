"""Starting a plan step as a local kato task, and the shared local-task starter."""

from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

from kato_core_lib.data_layers.service.local_task_service import LocalTaskRejected
from plan_queue_core_lib.plan_queue_core_lib.ports import LaunchRefused, LaunchRequest

from kato_webserver.local_task_starter import LocalTaskStarter
from kato_webserver.plan_queue_launcher import LocalStepLauncher, local_step_title


def _request(start_mode: str = 'implement') -> LaunchRequest:
    return LaunchRequest(
        plan_id='p' * 32, step_id='api', index=2, count=3, title='Add the endpoint',
        description='Step 2 of 3 …', repositories=('api',), source_task_id='UNA-1234',
        plan_title='Discounts', start_mode=start_mode, model='sonnet', effort='high',
    )


class LocalStepLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask('launcher-test')
        self.local_tasks = Mock()
        self.local_tasks.create.return_value = SimpleNamespace(id='LOCAL-7')
        self.starter = Mock()
        self.app.config['AGENT_SERVICE'] = SimpleNamespace(local_tasks=self.local_tasks)
        self.app.config['LOCAL_TASK_STARTER'] = self.starter
        self.launcher = LocalStepLauncher(self.app)

    def test_creates_and_starts_a_new_local_task_per_release(self) -> None:
        self.assertFalse(self.launcher.creates_at_approval)
        self.assertTrue(self.launcher.available())
        self.assertEqual(self.launcher.release(_request(), 'LOCAL-3'), 'LOCAL-7')
        self.local_tasks.create.assert_called_once_with(
            'UNA-1234 · Task 2: Add the endpoint', 'Step 2 of 3 …', ['api'],
        )
        self.starter.start.assert_called_once_with('LOCAL-7', 'implement', model='sonnet', effort='high')

    def test_an_unknown_start_mode_starts_by_implementing(self) -> None:
        self.launcher.release(_request(start_mode=''), '')
        self.assertEqual(self.starter.start.call_args.args[1], 'implement')

    def test_refusals(self) -> None:
        self.local_tasks.create.side_effect = LocalTaskRejected('repo not approved', unapproved=['api'])
        with self.assertRaises(LaunchRefused):
            self.launcher.release(_request(), '')
        with self.assertRaises(LaunchRefused):
            self.launcher.prepare(_request())
        self.app.config['LOCAL_TASK_STARTER'] = None
        self.assertFalse(self.launcher.available())
        with self.assertRaises(LaunchRefused):
            self.launcher.release(_request(), '')

    def test_withdraw_has_nothing_to_do(self) -> None:
        self.assertIsNone(self.launcher.withdraw('LOCAL-1', 'stopped'))

    def test_title(self) -> None:
        self.assertEqual(local_step_title(_request()), 'UNA-1234 · Task 2: Add the endpoint')


class LocalTaskStarterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask('starter-test')
        self.app.logger = logging.getLogger('starter-test')
        self.local_tasks = Mock()
        self.local_tasks.prepare.return_value = True
        self.app.config['AGENT_SERVICE'] = SimpleNamespace(local_tasks=self.local_tasks)
        self.set_mode, self.set_override, self.start_chat = Mock(), Mock(), Mock()
        self.starter = LocalTaskStarter(
            self.app, set_mode=self.set_mode, set_override=self.set_override,
            start_chat=self.start_chat, run_in_background=lambda work, name: work(),
        )

    def _start(self, mode, **kwargs):
        with patch('kato_core_lib.helpers.plan_mode_store.set_task_mode') as persisted:
            self.starter.start('LOCAL-1', mode, **kwargs)
        return persisted

    def test_implement_sets_the_mode_and_overrides_then_starts_the_chat(self) -> None:
        persisted = self._start('implement', model=' sonnet ', effort='high')
        persisted.assert_called_once()
        self.set_mode.assert_called_once()
        self.set_override.assert_any_call('TASK_MODEL_OVERRIDES', 'LOCAL-1', 'sonnet')
        self.set_override.assert_any_call('TASK_EFFORT_OVERRIDES', 'LOCAL-1', 'high')
        self.local_tasks.prepare.assert_called_once_with('LOCAL-1')
        self.start_chat.assert_called_once()

    def test_chat_mode_opens_the_task_but_sends_nothing(self) -> None:
        self._start('chat')
        self.start_chat.assert_not_called()

    def test_a_failed_prepare_never_starts_the_chat(self) -> None:
        self.local_tasks.prepare.return_value = False
        with self.assertLogs('starter-test', level='INFO'):
            self._start('plan')
        self.start_chat.assert_not_called()
        self.local_tasks.prepare.side_effect = RuntimeError('clone failed')
        with self.assertLogs('starter-test', level='ERROR'):
            self._start('plan')
        self.start_chat.assert_not_called()

    def test_the_default_background_runner_is_a_thread(self) -> None:
        starter = LocalTaskStarter(self.app, set_mode=Mock(), set_override=Mock(), start_chat=Mock())
        with patch('kato_webserver.local_task_starter.threading.Thread') as thread, \
                patch('kato_core_lib.helpers.plan_mode_store.set_task_mode'):
            starter.start('LOCAL-2', 'chat')
        self.assertEqual(thread.call_args.kwargs['name'], 'local-task-LOCAL-2')
        thread.return_value.start.assert_called_once()


if __name__ == '__main__':
    unittest.main()
