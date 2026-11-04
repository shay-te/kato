"""A repository added to a plan's main task reaches its sub-tasks not started.

Real git remotes and clones, the real RepositoryService / WorkspaceService /
LocalTaskService / TaskRepositoryService (the world of
``tests.test_local_task_branch_name.RealLocalTask``). The plan queue is a fake
that records what it is told: its lib tests what it does with it.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from kato_core_lib.data_layers.service.task_repository_service import TaskRepositoryService
from tests.test_local_task_branch_name import RealLocalTask


class _Queue(object):
    def __init__(self) -> None:
        self.added: list[tuple[str, list[str]]] = []
        self.error: Exception | None = None

    def add_repositories(self, source_task_id, repository_ids):
        if self.error is not None:
            raise self.error
        self.added.append((source_task_id, list(repository_ids)))
        return ['plan-1']


class AddToMainTaskTests(RealLocalTask, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._add_repository('lib')
        self.queue = _Queue()
        self.task_repositories = TaskRepositoryService(
            repository_service=self.repositories, task_service=self.service._task_service,
            workspace_manager=self.workspace, logger=mock.MagicMock(), plan_queue=self.queue,
        )
        self.main = str(self.service.create('The whole feature', 'All of it.', ['api']).id)
        self.assertTrue(self.service.prepare(self.main))

    def test_the_main_task_clones_it_and_its_plans_are_told(self) -> None:
        result = self.task_repositories.add_task_repositories(self.main, ['lib'])
        self.assertTrue(result['added'], result)
        self.assertTrue((Path(self.workspace.repository_path(self.main, 'lib')) / '.git').is_dir())
        self.assertEqual(self.queue.added, [(self.main, ['lib'])])
        self.assertEqual(result['plan_update'], {'plans': ['plan-1'], 'error': ''})

    def test_a_plan_that_cannot_be_told_is_reported_and_the_add_still_counts(self) -> None:
        self.queue.error = RuntimeError('queue down')
        result = self.task_repositories.add_task_repositories(self.main, ['lib'])
        self.assertTrue(result['added'])
        self.assertIn('queue down', result['plan_update']['error'])

    def test_no_plan_queue_is_nothing_to_tell(self) -> None:
        self.task_repositories._get_plan_queue = lambda: None
        result = self.task_repositories.add_task_repositories(self.main, ['lib'])
        self.assertEqual(result['plan_update'], {'plans': [], 'error': ''})


if __name__ == '__main__':
    unittest.main()
