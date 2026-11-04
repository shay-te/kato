"""A plan's sub-task merges into the branch of the task it was split from.

Real git (a bare remote, the operator's checkout, per-task workspace clones
under a real workspace service), the real ``RepositoryService`` and the real
``PlanMergeTarget``. Faked only at the edges git cannot play: the plan queue's
answer (its own lib tests it) and the git host's pull request API.
"""

from __future__ import annotations

import copy
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from workspace_core_lib.workspace_core_lib import WorkspaceCoreLib

from kato_core_lib.data_layers.service.plan_queue.merge_target import PlanMergeTarget
from kato_core_lib.data_layers.service.plan_queue.step_source import PlanStepSource
from kato_core_lib.data_layers.service.plan_queue.step_footer import subtask_description
from kato_core_lib.data_layers.service.repository_service import RepositoryService
from plan_queue_core_lib.plan_queue_core_lib.ports import LaunchRequest


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        'GIT_AUTHOR_NAME': 'plan-merge', 'GIT_AUTHOR_EMAIL': 'plan@merge.test',
        'GIT_COMMITTER_NAME': 'plan-merge', 'GIT_COMMITTER_EMAIL': 'plan@merge.test',
    }
    return subprocess.run(
        ['git', *args], cwd=str(cwd), env=env, check=True, capture_output=True, text=True,
    ).stdout.strip()


class _Queue(object):
    """The plan queue's one answer used here: which task a step's task was split from."""

    def __init__(self, sources: dict[str, str]) -> None:
        self.sources = {key.lower(): value for key, value in sources.items()}

    def source_of_step_task(self, task_id: str) -> str:
        return self.sources.get(str(task_id).lower(), '')


@unittest.skipUnless(shutil.which('git'), 'git binary not available on this system')
class _World(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='plan-merge-branch-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.origin = self.root / 'origin.git'
        self.origin.mkdir()
        _git(self.origin, 'init', '--bare', '--initial-branch', 'main')
        self.source = self.root / 'source' / 'api'
        self.source.parent.mkdir()
        _git(self.root / 'source', 'clone', str(self.origin), 'api')
        self._commit(self.source, 'README.md', 'seed\n', 'seed')
        _git(self.source, 'push', '-u', 'origin', 'main')
        self.workspaces_root = self.root / 'workspaces'
        self.workspaces = WorkspaceCoreLib(
            root=str(self.workspaces_root), max_parallel_tasks=1,
            metadata_filename='.kato-meta.json', preflight_log_filename='.kato-preflight.log',
        ).workspaces
        self.repository = SimpleNamespace(
            id='api', display_name='api', local_path=str(self.source),
            remote_url=str(self.origin), destination_branch='main', repo_slug='api', aliases=['api'],
        )
        self.service = RepositoryService([self.repository], 1)
        self.service.attach_kato_task_tags(
            lambda task_id: list(getattr(self.workspaces.get(task_id), 'tags', None) or []),
        )
        self.queue = _Queue({'UNA-2': 'UNA-1', 'UNA-3': 'UNA-1'})
        self.merge_target = PlanMergeTarget(
            step_source=PlanStepSource(queue=self.queue, workspace_manager=self.workspaces),
            repository_service=self.service,
        )
        self.service.attach_plan_merge_target(self.merge_target.branch_for)

    # ----- helpers -----

    @staticmethod
    def _commit(clone: Path, name: str, text: str, message: str) -> None:
        (clone / name).write_text(text, encoding='utf-8')
        _git(clone, 'add', name)
        _git(clone, 'commit', '-m', message)

    def _remote_commit(self, branch: str) -> str:
        line = _git(self.origin, 'for-each-ref', '--format=%(objectname)', f'refs/heads/{branch}')
        return line.strip()

    def _clone_for(self, task_id: str, **record) -> SimpleNamespace:
        """The task's workspace and its clone, as provisioning leaves them."""
        self.workspaces.create(task_id=task_id, repository_ids=['api'], **record)
        path = self.workspaces.repository_path(task_id, 'api')
        self.service.ensure_clone(self.repository, path)
        clone = copy.copy(self.repository)
        clone.local_path = str(path)
        return clone

    def _push_to_main(self, name: str, text: str) -> None:
        self._commit(self.source, name, text, f'add {name}')
        _git(self.source, 'push', 'origin', 'main')


class DestinationTests(_World):
    def test_until_the_plan_branch_is_on_the_remote_the_repository_destination_is_used(self) -> None:
        clone = self._clone_for('UNA-2')
        self.assertEqual(self.service.plan_merge_branch('UNA-2'), 'UNA-1')
        # Nothing pushed it yet: cut from and compared with main, where it will be cut from.
        self.assertEqual(self.service.destination_branch(clone), 'main')
        self.assertEqual(self._remote_commit('UNA-1'), '')

    def test_the_first_pull_request_cuts_the_plan_branch_from_the_destination(self) -> None:
        clone = self._clone_for('UNA-2')
        self._push_to_main('later.txt', 'main moved on\n')  # the remote is ahead of the clone

        self.assertEqual(self.service.put_plan_merge_branch_on_remote(clone), 'UNA-1')

        # Cut from main as the REMOTE holds it now, not the clone's stale copy.
        self.assertEqual(self._remote_commit('UNA-1'), self._remote_commit('main'))
        self.assertEqual(self.service.destination_branch(clone), 'UNA-1')
        # A second pull request (another repository, a retry) changes nothing.
        before = self._remote_commit('UNA-1')
        self.assertEqual(self.service.put_plan_merge_branch_on_remote(clone), 'UNA-1')
        self.assertEqual(self._remote_commit('UNA-1'), before)

    def test_a_plan_branch_someone_already_pushed_is_never_moved(self) -> None:
        _git(self.source, 'checkout', '-b', 'UNA-1')
        self._commit(self.source, 'parent.txt', 'parent work\n', 'UNA-1 parent work')
        _git(self.source, 'push', 'origin', 'UNA-1')
        _git(self.source, 'checkout', 'main')
        pushed = self._remote_commit('UNA-1')
        clone = self._clone_for('UNA-2')

        self.assertEqual(self.service.put_plan_merge_branch_on_remote(clone), 'UNA-1')

        self.assertEqual(self._remote_commit('UNA-1'), pushed)
        self.assertNotEqual(pushed, self._remote_commit('main'))

    def test_a_plan_branch_deleted_on_the_remote_is_cut_again_from_the_destination(self) -> None:
        # The clone still remembers origin/UNA-1 after the remote dropped it:
        # what is cut from must be the repository's own destination, never
        # what the stale clone believes it merges into.
        clone = self._clone_for('UNA-2')
        self.service.put_plan_merge_branch_on_remote(clone)
        _git(self.origin, 'update-ref', '-d', 'refs/heads/UNA-1')
        self._push_to_main('after.txt', 'main moved on\n')

        self.assertEqual(self.service.put_plan_merge_branch_on_remote(clone), 'UNA-1')

        self.assertEqual(self._remote_commit('UNA-1'), self._remote_commit('main'))

    def test_the_main_tasks_own_branch_name_is_the_one_merged_into(self) -> None:
        self.workspaces.create(task_id='UNA-1', repository_ids=['api'])
        self.workspaces.add_tag('UNA-1', 'kato:branch:feature/big-change')
        clone = self._clone_for('UNA-2')

        self.assertEqual(self.service.put_plan_merge_branch_on_remote(clone), 'feature/big-change')

        self.assertEqual(self._remote_commit('feature/big-change'), self._remote_commit('main'))
        self.assertEqual(self._remote_commit('UNA-1'), '')

    def test_any_other_task_merges_into_the_destination_and_nothing_is_pushed(self) -> None:
        clone = self._clone_for('UNA-9')
        self.assertEqual(self.service.plan_merge_branch('UNA-9'), '')
        self.assertEqual(self.service.put_plan_merge_branch_on_remote(clone), '')
        self.assertEqual(self.service.destination_branch(clone), 'main')
        self.assertEqual(_git(self.origin, 'for-each-ref', '--format=%(refname)', 'refs/heads'), 'refs/heads/main')
        # The operator's own checkout is no task's clone, whatever its folder.
        self.assertEqual(self.service.destination_branch(self.repository), 'main')

    def test_the_diff_base_of_a_sub_tasks_clone_is_its_plan_branch(self) -> None:
        clone = self._clone_for('UNA-2')
        self.assertEqual(self.service.destination_branch_of_clone(self.repository, clone.local_path), 'main')
        self.service.put_plan_merge_branch_on_remote(clone)
        self.assertEqual(self.service.destination_branch_of_clone(self.repository, clone.local_path), 'UNA-1')
        other = self._clone_for('UNA-9')
        self.assertEqual(self.service.destination_branch_of_clone(self.repository, other.local_path), 'main')

    def test_a_branch_name_git_would_misread_is_never_used(self) -> None:
        self.queue.sources['una-4'] = 'UNA-1'
        self.workspaces.create(task_id='UNA-1', repository_ids=['api'])
        self.workspaces.add_tag('UNA-1', 'kato:branch:-x')
        with self.assertLogs(self.service.logger, 'WARNING'):
            self.assertEqual(self.service.plan_merge_branch('UNA-4'), '')

    def test_a_merge_target_that_fails_is_logged_and_the_destination_used(self) -> None:
        self.service.attach_plan_merge_target(MagicMock(side_effect=RuntimeError('queue broken')))
        clone = self._clone_for('UNA-2')
        with self.assertLogs(self.service.logger, 'ERROR'):
            self.assertEqual(self.service.destination_branch(clone), 'main')


class BranchPreparationTests(_World):
    def test_a_dependent_sub_task_starts_from_the_work_merged_into_the_plan_branch(self) -> None:
        # UNA-2 was merged into UNA-1; UNA-3, waiting on it, starts now.
        _git(self.source, 'checkout', '-b', 'UNA-1')
        self._commit(self.source, 'step-one.txt', 'UNA-2 work\n', 'UNA-2 step one')
        _git(self.source, 'push', 'origin', 'UNA-1')
        _git(self.source, 'checkout', 'main')
        clone = self._clone_for('UNA-3')

        self.service.prepare_task_branches([clone], {'api': 'UNA-3'})

        path = Path(clone.local_path)
        self.assertEqual(_git(path, 'branch', '--show-current'), 'UNA-3')
        self.assertTrue((path / 'step-one.txt').is_file())
        self.assertEqual(clone.destination_branch, 'UNA-1')

    def test_the_configured_destination_does_not_hide_the_plan_branch(self) -> None:
        # The clone is a copy of the inventory entry, which carries its
        # configured destination: branch preparation must still ask.
        _git(self.source, 'push', 'origin', 'main:UNA-1')
        _git(self.source, 'checkout', '-b', 'tmp')
        self._commit(self.source, 'on-plan.txt', 'x\n', 'on the plan branch')
        _git(self.source, 'push', 'origin', 'tmp:UNA-1')
        clone = self._clone_for('UNA-2')
        self.assertEqual(clone.destination_branch, 'main')

        self.service.prepare_task_branches([clone], {'api': 'UNA-2'})

        self.assertTrue((Path(clone.local_path) / 'on-plan.txt').is_file())


class MergeButtonTests(_World):
    """The header's "merge the default branch": a sub-task merges its plan's branch."""

    def _sibling_merged_into_plan_branch(self) -> None:
        # Created AFTER the sub-task's clone, as the first sibling's pull request does.
        _git(self.source, 'checkout', '-b', 'plan-work')
        self._commit(self.source, 'sibling.txt', 'merged sibling\n', 'UNA-4 work')
        _git(self.source, 'push', 'origin', 'plan-work:UNA-1')
        _git(self.source, 'checkout', 'main')

    def test_a_sub_task_merges_the_main_tasks_branch_not_master(self) -> None:
        clone = self._clone_for('UNA-2')
        self.service.prepare_task_branches([clone], {'api': 'UNA-2'})
        self._sibling_merged_into_plan_branch()
        self._push_to_main('main-only.txt', 'not part of the plan\n')

        result = self.service.merge_default_branch_into_clone(clone, 'UNA-2')

        path = Path(clone.local_path)
        self.assertEqual((result['merged'], result['default_branch']), (True, 'UNA-1'))
        self.assertTrue((path / 'sibling.txt').is_file())
        self.assertFalse((path / 'main-only.txt').exists())

    def test_before_the_main_branch_is_on_the_remote_master_is_where_it_starts(self) -> None:
        clone = self._clone_for('UNA-2')
        self.service.prepare_task_branches([clone], {'api': 'UNA-2'})
        self._push_to_main('main-only.txt', 'x\n')

        result = self.service.merge_default_branch_into_clone(clone, 'UNA-2')

        self.assertEqual(result['default_branch'], 'main')
        self.assertTrue((Path(clone.local_path) / 'main-only.txt').is_file())

    def test_any_other_task_still_merges_master(self) -> None:
        clone = self._clone_for('UNA-9')
        self.service.prepare_task_branches([clone], {'api': 'UNA-9'})
        self._sibling_merged_into_plan_branch()
        self._push_to_main('main-only.txt', 'x\n')

        result = self.service.merge_default_branch_into_clone(clone, 'UNA-9')

        self.assertEqual(result['default_branch'], 'main')
        self.assertFalse((Path(clone.local_path) / 'sibling.txt').exists())


class PullRequestTests(_World):
    def _publish(self, clone) -> tuple[dict, MagicMock]:
        data_access = MagicMock()
        data_access.create_pull_request.return_value = {'id': '7', 'url': 'https://git.example/pr/7'}
        with patch.object(self.service, '_prepare_pull_request_api'), \
                patch.object(self.service, '_pull_request_data_access', return_value=data_access):
            result = self.service.create_pull_request(clone, 'UNA-2: step', 'UNA-2')
        return result, data_access

    def _work_on(self, clone, task_id: str) -> None:
        path = Path(clone.local_path)
        self.service.prepare_task_branches([clone], {'api': task_id})
        self._commit(path, f'{task_id}.txt', 'work\n', f'{task_id} work')

    def test_a_sub_tasks_pull_request_goes_into_the_main_tasks_branch(self) -> None:
        clone = self._clone_for('UNA-2')
        self._work_on(clone, 'UNA-2')

        result, data_access = self._publish(clone)

        kwargs = data_access.create_pull_request.call_args.kwargs
        self.assertEqual((kwargs['source_branch'], kwargs['destination_branch']), ('UNA-2', 'UNA-1'))
        self.assertEqual(result['destination_branch'], 'UNA-1')
        self.assertEqual(self._remote_commit('UNA-1'), self._remote_commit('main'))
        self.assertNotEqual(self._remote_commit('UNA-2'), '')

    def test_any_other_tasks_pull_request_goes_into_the_destination(self) -> None:
        self.queue.sources.clear()
        clone = self._clone_for('UNA-2')
        self._work_on(clone, 'UNA-2')

        _result, data_access = self._publish(clone)

        self.assertEqual(data_access.create_pull_request.call_args.kwargs['destination_branch'], 'main')
        self.assertEqual(self._remote_commit('UNA-1'), '')


class MergeTargetTests(unittest.TestCase):
    """Which task a sub-task was split from, without git."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='plan-merge-target-')
        self.addCleanup(tmp.cleanup)
        self.workspaces = WorkspaceCoreLib(
            root=str(Path(tmp.name) / 'workspaces'), max_parallel_tasks=1,
            metadata_filename='.kato-meta.json', preflight_log_filename='.kato-preflight.log',
        ).workspaces
        self.queue = _Queue({})
        self.repository_service = SimpleNamespace(
            task_branch_name=lambda task_id: {'UNA-5': 'feature/five'}.get(task_id, task_id),
        )
        self.source = PlanStepSource(queue=self.queue, workspace_manager=self.workspaces)
        self.target = PlanMergeTarget(step_source=self.source, repository_service=self.repository_service)

    def test_the_queue_answers_first(self) -> None:
        self.queue.sources['una-2'] = 'UNA-5'
        self.assertEqual(self.source.source_of('UNA-2'), 'UNA-5')
        self.assertEqual(self.target.branch_for('UNA-2'), 'feature/five')

    def test_a_local_step_announced_before_the_queue_records_it(self) -> None:
        self.source.announce('LOCAL-4', 'UNA-1')
        self.assertEqual(self.target.branch_for('local-4'), 'UNA-1')
        # Once the queue knows the task, the announcement is dropped.
        self.queue.sources['local-4'] = 'UNA-1'
        self.assertEqual(self.target.branch_for('LOCAL-4'), 'UNA-1')
        self.queue.sources.clear()
        self.assertEqual(self.target.branch_for('LOCAL-4'), '')
        self.source.announce('', 'UNA-1')
        self.source.announce('LOCAL-6', '')
        self.assertEqual(self.source.source_of('LOCAL-6'), '')

    def test_a_tracker_sub_task_of_a_plan_not_followed_here_reads_its_footer(self) -> None:
        request = LaunchRequest(
            plan_id='0' * 32, step_id='api', index=1, count=2, title='Add it', description='Do it.',
            repositories=('api',), source_task_id='PROJ-1', plan_title='Plan',
        )
        self.workspaces.create(
            task_id='PROJ-2', repository_ids=['api'], task_description=subtask_description(request, ['api']),
        )
        self.assertEqual(self.target.branch_for('PROJ-2'), 'PROJ-1')
        self.workspaces.create(task_id='PROJ-3', repository_ids=['api'], task_description='a plain ticket')
        self.assertEqual(self.target.branch_for('PROJ-3'), '')
        self.assertEqual(self.target.branch_for('PROJ-404'), '')  # no workspace at all

    def test_a_task_is_never_merged_into_itself(self) -> None:
        self.queue.sources['una-1'] = 'UNA-1'
        self.assertEqual(self.target.branch_for('UNA-1'), '')
        self.assertEqual(self.source.source_of('UNA-1'), '')  # never its own source
        # A sub-task given its main task's branch name would merge into itself.
        self.queue.sources['una-6'] = 'UNA-5'
        self.repository_service.task_branch_name = lambda task_id: 'feature/five'
        self.assertEqual(self.target.branch_for('UNA-6'), '')
        self.assertEqual(self.target.branch_for(''), '')

    def test_without_a_queue_or_workspaces_only_announcements_count(self) -> None:
        source = PlanStepSource(queue=None, workspace_manager=None)
        target = PlanMergeTarget(step_source=source, repository_service=self.repository_service)
        self.assertEqual(target.branch_for('UNA-2'), '')
        source.announce('UNA-2', 'UNA-1')
        self.assertEqual(target.branch_for('UNA-2'), 'UNA-1')


class HostWiringTests(unittest.TestCase):
    def test_the_repository_service_asks_the_agent_services_merge_target(self) -> None:
        import inspect

        from kato_core_lib.kato_core_lib import KatoCoreLib

        self.assertEqual(KatoCoreLib._plan_merge_branch(SimpleNamespace(service=None), 'UNA-2'), '')
        target = MagicMock()
        target.branch_for.return_value = 'UNA-1'
        host = SimpleNamespace(service=SimpleNamespace(plan_merge_target=target))
        self.assertEqual(KatoCoreLib._plan_merge_branch(host, 'UNA-2'), 'UNA-1')
        target.branch_for.assert_called_once_with('UNA-2')
        self.assertIn('attach_plan_merge_target', inspect.getsource(KatoCoreLib))


if __name__ == '__main__':
    unittest.main()
