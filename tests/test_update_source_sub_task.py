"""Update source on a plan's sub-task puts the WHOLE plan on the operator's disk.

Operator report: "when I do update source on a subtask and it changed only one
repo, the other repos tagged to the parent task must be shifted to its parent
branch in my working directory too, so the task can be tested". They were
skipped ("no changes in workspace clone"), so the source stayed on master —
without the sub-tasks already merged into the main task's branch.

Real remotes, real per-task clones, the operator's real source checkouts.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from kato_core_lib.data_layers.service.task_publish_service import TaskPublishService
from tests.test_local_task_branch_name import RealLocalTask, _git

MAIN = 'LOCAL-1'
SUB = 'LOCAL-2'


class _PlanFixture(RealLocalTask):
    """A main task in three repositories and its sub-task in two of them."""

    def setUp(self) -> None:
        super().setUp()
        for name in ('web', 'core'):
            self._add_repository(name)
        # The main task works in all three; the plan's sub-task in two of them.
        self.service.create('The plan', 'All of it.', ['api', 'web', 'core'], branch='')
        self.service.create('Step 2', 'The api part.', ['api', 'web'], branch='')
        self.parents = {SUB: MAIN}
        self.repositories.attach_plan_merge_target(
            lambda task_id: MAIN if task_id == SUB else '',
        )
        self.publisher = TaskPublishService(
            repository_service=self.repositories,
            task_service=mock.MagicMock(),
            task_state_service=mock.MagicMock(),
            task_publisher=mock.MagicMock(),
            workspace_manager=self.workspace,
            plan_parent_of=lambda task_id: self.parents.get(task_id, ''),
            logger=mock.MagicMock(),
        )

    def _remote(self, name: str) -> Path:
        return self.root / 'remote' / f'{name}.git'

    def _merge_into_main_branch(self, *names: str) -> None:
        """An earlier sub-task, merged: the main task's branch on each remote."""
        _git(self.seed, 'checkout', '-q', '-B', MAIN, 'master')
        (self.seed / 'step1.py').write_text('STEP_1 = True\n', encoding='utf-8')
        _git(self.seed, 'add', '-A')
        _git(self.seed, 'commit', '-qm', 'step 1, merged')
        for name in names:
            _git(self.seed, 'push', '-q', str(self._remote(name)), f'{MAIN}:{MAIN}')

    def _sub_task_changes_api(self) -> None:
        self.assertTrue(self.service.prepare(SUB))
        self._commit_work(Path(self.workspace.repository_path(SUB, 'api')))

    def _source_branch(self, name: str) -> str:
        return _git(self.source_root / name, 'rev-parse', '--abbrev-ref', 'HEAD')


class UpdateSourceOnSubTaskTests(_PlanFixture, unittest.TestCase):
    def test_the_changed_repo_gets_the_sub_task_and_every_other_the_main_branch(self) -> None:
        self._merge_into_main_branch('api', 'web', 'core')
        self._sub_task_changes_api()

        result = self.publisher.update_source_for_task(SUB)

        self.assertTrue(result['updated'])
        self.assertEqual(result['updated_repositories'], ['api'])
        self.assertEqual(result['main_branch'], MAIN)
        # web: the sub-task's, unchanged; core: only the main task's.
        self.assertEqual(result['main_branch_repositories'], ['web', 'core'])
        self.assertEqual(result['failed_repositories'], [])
        self.assertEqual(self._source_branch('api'), SUB)
        self.assertEqual(self._source_branch('web'), MAIN)
        self.assertEqual(self._source_branch('core'), MAIN)
        # The plan's merged work is on disk with the sub-task's own.
        self.assertTrue((self.source_root / 'api' / 'login.py').is_file())
        self.assertTrue((self.source_root / 'api' / 'step1.py').is_file())  # cut from the main branch
        self.assertTrue((self.source_root / 'core' / 'step1.py').is_file())

    def test_a_sub_task_with_no_work_yet_still_puts_the_plan_on_disk(self) -> None:
        self._merge_into_main_branch('api', 'web', 'core')
        self.assertTrue(self.service.prepare(SUB))  # nothing committed

        result = self.publisher.update_source_for_task(SUB)

        self.assertTrue(result['updated'])  # moved, so not "Nothing to update"
        self.assertEqual(result['updated_repositories'], [])
        self.assertEqual(result['main_branch_repositories'], ['api', 'web', 'core'])
        self.assertEqual({self._source_branch(n) for n in ('api', 'web', 'core')}, {MAIN})

    def test_a_repo_whose_main_branch_is_not_on_the_remote_is_left_and_says_why(self) -> None:
        self._merge_into_main_branch('api', 'web')  # nothing merged in core yet
        self._sub_task_changes_api()

        result = self.publisher.update_source_for_task(SUB)

        self.assertEqual(result['main_branch_repositories'], ['web'])
        self.assertEqual(result['skipped_repositories'], [{
            'repository_id': 'core',
            'reason': "LOCAL-1, the main task's branch, is not on the remote yet",
        }])
        self.assertEqual(self._source_branch('core'), 'master')  # untouched

    def test_a_task_that_is_no_sub_task_skips_its_unchanged_repos_as_before(self) -> None:
        self._merge_into_main_branch('api', 'web', 'core')
        self.parents = {}
        self._sub_task_changes_api()

        result = self.publisher.update_source_for_task(SUB)

        self.assertEqual(result['updated_repositories'], ['api'])
        self.assertEqual(result['main_branch_repositories'], [])
        self.assertEqual(result['main_branch'], '')
        self.assertEqual(
            [entry['repository_id'] for entry in result['skipped_repositories']], ['web'],
        )
        self.assertEqual(self._source_branch('web'), 'master')
        self.assertEqual(self._source_branch('core'), 'master')

    def test_a_dirty_source_blocks_only_its_own_repo(self) -> None:
        self._merge_into_main_branch('api', 'web', 'core')
        self._sub_task_changes_api()
        # The operator has a change in core that the main branch would overwrite.
        _git(self.source_root / 'core', 'checkout', '-q', 'master')
        (self.source_root / 'core' / 'step1.py').write_text('MINE = 1\n', encoding='utf-8')

        result = self.publisher.update_source_for_task(SUB)

        self.assertEqual(result['updated_repositories'], ['api'])
        self.assertEqual(result['main_branch_repositories'], ['web'])
        blocked = [w for w in result['warnings'] if w.get('blocked')]
        self.assertEqual([w['repository_id'] for w in blocked], ['core'])
        self.assertEqual((self.source_root / 'core' / 'step1.py').read_text(), 'MINE = 1\n')


class UpdateSourceOfOneRepositoryTests(_PlanFixture, unittest.TestCase):
    """The Changes tab's per-repository Update source: "I just need to verify
    a small client change, but it pulls the backend too, which I don't want —
    my Docker is building from it". Only the repository asked for moves."""

    def _work_in(self, *names: str) -> None:
        self.assertTrue(self.service.prepare(SUB))
        for name in names:
            clone = Path(self.workspace.repository_path(SUB, name))
            _git(clone, 'config', 'user.email', 't@example.com')
            _git(clone, 'config', 'user.name', 'test')
            (clone / f'{name}_change.py').write_text('CHANGED = True\n', encoding='utf-8')
            _git(clone, 'add', '-A')
            _git(clone, 'commit', '-qm', f'{name} change')

    def test_only_the_repository_asked_for_is_pushed_and_switched(self) -> None:
        self._merge_into_main_branch('api', 'web', 'core')
        self.parents = {}  # an ordinary task
        self._work_in('api', 'web')

        result = self.publisher.update_source_for_task(SUB, repository_ids=['web'])

        self.assertEqual(result['updated_repositories'], ['web'])
        self.assertEqual(result['pushed']['pushed_repositories'], ['web'])
        self.assertEqual(self._source_branch('web'), SUB)
        self.assertEqual(self._source_branch('api'), 'master')  # untouched
        # Not even pushed: the api work is still only in the task's clone.
        self.assertEqual(_git(self._remote('api'), 'branch', '--list', SUB), '')

    def test_on_a_sub_task_a_main_task_repository_alone_goes_to_the_main_branch(self) -> None:
        self._merge_into_main_branch('api', 'web', 'core')
        self._work_in('api')

        result = self.publisher.update_source_for_task(SUB, repository_ids=['CORE'])

        self.assertEqual(result['updated_repositories'], [])
        self.assertEqual(result['main_branch_repositories'], ['core'])
        self.assertEqual(self._source_branch('core'), MAIN)
        self.assertEqual(self._source_branch('api'), 'master')
        self.assertEqual(self._source_branch('web'), 'master')

    def test_a_repository_the_task_does_not_have_is_said_and_nothing_moves(self) -> None:
        self._merge_into_main_branch('api', 'web', 'core')
        self._work_in('api')

        result = self.publisher.update_source_for_task(SUB, repository_ids=['billing'])

        self.assertEqual(result['failed_repositories'], [
            {'repository_id': 'billing', 'error': "not one of this task's repositories"},
        ])
        self.assertFalse(result['updated'])
        self.assertEqual({self._source_branch(n) for n in ('api', 'web', 'core')}, {'master'})


class WiringTests(unittest.TestCase):
    def test_the_publish_service_asks_the_plan_which_task_a_sub_task_came_from(self) -> None:
        from kato_core_lib.data_layers.service.agent_service import AgentService
        from tests.test_agent_service_more_coverage import _kwargs

        service = AgentService(**_kwargs())
        with mock.patch.object(service._plan_step_source, 'source_of', return_value='UNA-1') as source_of:
            self.assertEqual(service.publish._plan_parent_of('UNA-2'), 'UNA-1')
        source_of.assert_called_once_with('UNA-2')


if __name__ == '__main__':
    unittest.main()
