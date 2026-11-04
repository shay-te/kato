"""The main task pulls each sub-task merged into its branch — said in the log,
and wired into the plan queue."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from kato_core_lib.data_layers.service.plan_queue.main_task_follows import MainTaskFollowsMerges

PLAN = SimpleNamespace(source_task_id='UNA-1')
STEP = SimpleNamespace(task_id='UNA-2', id='api')


class MainTaskFollowsMergesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = Mock()
        self.later = []

    def follow(self, outcome):
        follower = MainTaskFollowsMerges(pull_task=lambda task_id: outcome, logger=self.logger, run_later=self.later.append)
        said = self.logger.info.call_count + self.logger.warning.call_count
        follower(PLAN, STEP)
        self.assertEqual(self.logger.info.call_count + self.logger.warning.call_count, said)  # off the tick
        self.later.pop()()

    def test_what_was_pulled_skipped_and_failed_is_logged(self) -> None:
        self.follow({
            'pulled_repositories': [{'repository_id': 'api', 'commits_pulled': 3}],
            'skipped_repositories': [{'repository_id': 'web', 'reason': 'dirty working tree'}],
            'failed_repositories': [{'repository_id': 'core', 'error': 'remote unreachable'}],
        })
        infos = [call.args for call in self.logger.info.call_args_list]
        self.assertIn(('%s was merged: %s pulled %s commit(s) in %s', 'UNA-2', 'UNA-1', 3, 'api'), infos)
        self.assertIn(('%s was merged, but %s did not pull %s: %s', 'UNA-2', 'UNA-1', 'web', 'dirty working tree'), infos)
        self.logger.warning.assert_called_once()

    def test_a_main_task_with_no_workspace_here_is_said_once(self) -> None:
        self.follow({'error': 'no workspace context for this task'})
        self.assertIn('no workspace context', self.logger.info.call_args.args[-1])
        self.follow(None)  # nothing answered: nothing to say
        self.assertEqual(self.logger.info.call_count, 1)

    def test_the_default_runs_off_the_tick_on_its_own_thread(self) -> None:
        done = []
        follower = MainTaskFollowsMerges(pull_task=lambda task_id: done.append(task_id) or {}, logger=self.logger)
        follower(PLAN, SimpleNamespace(task_id='', id='api'))
        for _ in range(200):
            if done:
                break
            import time
            time.sleep(0.01)
        self.assertEqual(done, ['UNA-1'])


class WiringTests(unittest.TestCase):
    def test_the_queue_is_told_to_pull_the_main_task(self) -> None:
        from kato_core_lib.data_layers.service.plan_queue.wiring import build_plan_queue_service
        pull = Mock()
        with patch('kato_core_lib.data_layers.service.plan_queue.wiring.plan_queues_root', return_value='/tmp/no-such-plans'):
            service = build_plan_queue_service(
                workspace_manager=None, repository_service=Mock(), local_task_store=Mock(), logger=Mock(),
                pull_task=pull,
            )
            bare = build_plan_queue_service(
                workspace_manager=None, repository_service=Mock(), local_task_store=Mock(), logger=Mock(),
            )
        self.assertIsInstance(service._ticker._merged_listener, MainTaskFollowsMerges)
        self.assertIsNone(bare._ticker._merged_listener)


if __name__ == '__main__':
    unittest.main()
