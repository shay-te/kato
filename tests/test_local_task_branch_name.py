"""A task written in kato can name its own branch.

Operator report: a friend adding tasks by hand had no way to choose the branch,
kato made ``LOCAL-1``. The name is optional and goes in with the task. The risk
was never the form: "the branch is the task id" was assumed in places that only
hold a task id, and those would have quietly put a clone back on ``LOCAL-1`` or
looked for pull requests under the wrong name.

Real git repositories, real clones, the real services. The tracker is the one
thing absent, and a local task must never reach it.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
from kato_core_lib.data_layers.service.local_task_routing import (
    LocalAwareTaskService,
    LocalAwareTaskStateService,
)
from kato_core_lib.data_layers.service.local_task_service import (
    LocalTaskRejected,
    LocalTaskService,
)
from kato_core_lib.data_layers.service.repository_service import RepositoryService
from kato_core_lib.data_layers.service.task_publish_service import TaskPublishService
from kato_core_lib.data_layers.service.wait_planning_service import WaitPlanningService
from kato_core_lib.data_layers.service.workspace_manager import build_workspace_manager_from_config
from kato_core_lib.kato_core_lib import KatoCoreLib
from kato_core_lib.main import _reconcile_workspace_branches
from kato_webserver import app as app_module
from local_task_core_lib.local_task_core_lib.store import LocalTaskStore

BRANCH = 'feature/login'


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ['git', *args], cwd=str(cwd), check=True, capture_output=True, text=True,
    ).stdout.strip()


class _NoTracker(object):
    def __getattr__(self, name):
        raise AssertionError(f'the tracker was called: {name}')


class RealLocalTask(object):
    """A real task in a real clone of a real remote, for a TestCase to mix in."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.remote = self.root / 'remote' / 'api.git'
        self.remote.mkdir(parents=True)
        _git(self.remote, 'init', '-q', '--bare', '-b', 'master')
        seed = self.root / 'seed'
        _git(self.root, 'clone', '-q', str(self.remote), 'seed')
        _git(seed, 'config', 'user.email', 't@example.com')
        _git(seed, 'config', 'user.name', 'test')
        _git(seed, 'checkout', '-q', '-b', 'master')
        (seed / 'app.py').write_text('VALUE = 1\n', encoding='utf-8')
        _git(seed, 'add', '-A')
        _git(seed, 'commit', '-qm', 'initial')
        _git(seed, 'push', '-q', 'origin', 'master')
        self.seed = seed

        self.source_root = self.root / 'dev'
        self.source_root.mkdir()
        _git(self.source_root, 'clone', '-q', str(self.remote), 'api')
        self.workspaces_root = self.root / 'workspaces'
        self.workspaces_root.mkdir()
        env = mock.patch.dict(os.environ, {
            'REPOSITORY_ROOT_PATH': str(self.source_root),
            'KATO_WORKSPACES_ROOT': str(self.workspaces_root),
        })
        env.start()
        self.addCleanup(env.stop)

        config = SimpleNamespace(project='P', assignee='me', issue_states=['Todo'])
        access = TaskDataAccess(config, _NoTracker())
        self.store = LocalTaskStore(self.root / 'local_tasks.json')
        task_service = LocalAwareTaskService(config, access, self.store)
        # Production's builder: only its metadata name marks a per-task clone,
        # and kato treats any other clone as a shared checkout.
        self.workspace = build_workspace_manager_from_config(
            SimpleNamespace(workspaces_root=str(self.workspaces_root)), 'claude',
        )
        # Nothing here is stubbed: a stub for pull request setup is what hid a
        # real clone sitting on master. These remotes are folders, a git host
        # kato does not recognise, and that must still get a branch.
        self.repositories = RepositoryService(
            SimpleNamespace(repository_root_path=str(self.source_root)), 3,
        )
        # The same wiring as ``KatoCoreLib``.
        self.repositories.attach_kato_task_tags(
            lambda task_id: KatoCoreLib._kato_tags_for_task(
                SimpleNamespace(workspace_manager=self.workspace), task_id,
            ),
        )
        self.planning = WaitPlanningService(
            session_manager=None, repository_service=self.repositories,
            task_state_service=LocalAwareTaskStateService(config, access, self.store),
            workspace_manager=self.workspace,
        )
        self.service = LocalTaskService(
            task_service=task_service, repository_service=self.repositories,
            workspace_manager=self.workspace,
            prepare_workspace=self.planning.resolve_planning_context,
        )
        self.task_state = mock.MagicMock()
        self.publisher = TaskPublishService(
            repository_service=self.repositories,
            task_service=task_service,
            task_state_service=self.task_state,
            task_publisher=mock.MagicMock(),
            workspace_manager=self.workspace,
            logger=mock.MagicMock(),
        )

    def _add_repository(self, name: str) -> None:
        remote = self.root / 'remote' / f'{name}.git'
        remote.mkdir()
        _git(remote, 'init', '-q', '--bare', '-b', 'master')
        _git(self.seed, 'push', '-q', str(remote), 'master')
        _git(self.source_root, 'clone', '-q', str(remote), name)
        self.repositories._repositories = None

    def _create(self, branch: str = BRANCH) -> str:
        task = self.service.create('Add login', 'Do it.', ['api'], branch=branch)
        return str(task.id)

    def _prepared(self, branch: str = BRANCH) -> tuple[str, Path]:
        task_id = self._create(branch)
        self.assertTrue(self.service.prepare(task_id))
        return task_id, Path(self.workspace.repository_path(task_id, 'api'))

    def _branches(self, clone: Path) -> list[str]:
        return _git(clone, 'branch', '--format=%(refname:short)').split()

    def _commit_work(self, clone: Path) -> None:
        _git(clone, 'config', 'user.email', 't@example.com')
        _git(clone, 'config', 'user.name', 'test')
        (clone / 'login.py').write_text('LOGIN = True\n', encoding='utf-8')
        _git(clone, 'add', '-A')
        _git(clone, 'commit', '-qm', 'login')


class LocalTaskBranchNameTests(RealLocalTask, unittest.TestCase):
    # ----- the task works on the branch it was given -----

    def test_the_clone_is_on_the_named_branch_and_no_other(self) -> None:
        task_id, clone = self._prepared()

        self.assertEqual(_git(clone, 'rev-parse', '--abbrev-ref', 'HEAD'), BRANCH)
        self.assertNotIn(task_id, self._branches(clone))
        self.assertTrue((clone / 'app.py').is_file())

    def test_with_no_name_the_branch_is_the_task_id_as_always(self) -> None:
        task_id, clone = self._prepared('')

        self.assertEqual(task_id, 'LOCAL-1')
        self.assertEqual(_git(clone, 'rev-parse', '--abbrev-ref', 'HEAD'), 'LOCAL-1')

    def test_creating_a_task_logs_no_error(self) -> None:
        # It has no id while its repositories are resolved, so there is no
        # workspace to read tags from. That is normal, not a traceback.
        with mock.patch.object(self.repositories.logger, 'exception') as logged:
            self._create()

        logged.assert_not_called()

    def test_the_operators_own_checkout_gets_no_branch(self) -> None:
        self._prepared()

        self.assertNotIn(BRANCH, self._branches(self.source_root / 'api'))

    def test_the_name_survives_a_later_provisioning_pass(self) -> None:
        task_id, _clone = self._prepared()

        self.workspace.create(task_id=task_id, repository_ids=['api'])

        self.assertEqual(self.repositories.task_branch_name(task_id), BRANCH)

    def test_a_branch_already_on_the_remote_is_continued(self) -> None:
        _git(self.seed, 'checkout', '-q', '-b', BRANCH)
        (self.seed / 'started.py').write_text('STARTED = 1\n', encoding='utf-8')
        _git(self.seed, 'add', '-A')
        _git(self.seed, 'commit', '-qm', 'started')
        _git(self.seed, 'push', '-q', 'origin', BRANCH)

        _task_id, clone = self._prepared()

        self.assertEqual(_git(clone, 'rev-parse', '--abbrev-ref', 'HEAD'), BRANCH)
        self.assertTrue((clone / 'started.py').is_file())

    # ----- a branch needs git, not a pull request host -----

    def test_a_repository_with_no_token_for_its_host_still_gets_its_branch(self) -> None:
        task_id = self._create()
        inventory_entry = self.repositories.get_repository('api')
        inventory_entry.provider = 'github'

        self.assertTrue(self.service.prepare(task_id))

        clone = Path(self.workspace.repository_path(task_id, 'api'))
        self.assertEqual(_git(clone, 'rev-parse', '--abbrev-ref', 'HEAD'), BRANCH)

    def test_a_chat_still_sets_up_pull_requests_when_it_can(self) -> None:
        # Optional, not skipped: this is what puts the token on the repository.
        repository = self.repositories.get_repository('api')
        with mock.patch.object(self.repositories, '_prepare_pull_request_api') as setup:
            self.repositories.prepare_task_repositories([repository], pull_requests_required=False)

        setup.assert_called_once_with(repository)

    def test_a_ticket_kato_runs_by_itself_still_needs_a_host_it_knows(self) -> None:
        # Unchanged on purpose: that flow ends in a pull request, so it fails up
        # front and says why on the ticket, rather than after the work is done.
        repository = self.repositories.get_repository('api')
        with self.assertRaisesRegex(ValueError, 'pull request provider'):
            self.repositories.prepare_task_repositories([repository])

    def test_a_task_that_cannot_be_prepared_says_so_on_the_task(self) -> None:
        with mock.patch.object(
            self.repositories, 'prepare_task_branches', side_effect=RuntimeError('git said no'),
        ):
            task_id = self._create()
            self.assertFalse(self.service.prepare(task_id))

        lines = [entry[-1] for entry in self.workspace.read_preflight_log(task_id)]
        self.assertIn('the agent was not started', lines[-1])

    # ----- nothing puts it back on the task id -----

    def test_the_changes_tab_heals_a_drifted_clone_onto_the_named_branch(self) -> None:
        task_id, clone = self._prepared()
        _git(clone, 'checkout', '-q', 'master')

        branch = app_module._task_branch(self.workspace, task_id)
        app_module._compute_repo_diff('api', str(clone), branch=branch, agent_service=None)

        self.assertEqual(_git(clone, 'rev-parse', '--abbrev-ref', 'HEAD'), BRANCH)

    def test_the_boot_reconcile_heals_a_drifted_clone_onto_the_named_branch(self) -> None:
        task_id, clone = self._prepared()
        _git(clone, 'checkout', '-q', 'master')

        _reconcile_workspace_branches(
            SimpleNamespace(workspace_manager=self.workspace, logger=mock.MagicMock()),
        )

        self.assertEqual(_git(clone, 'rev-parse', '--abbrev-ref', 'HEAD'), BRANCH)
        self.assertNotIn(task_id, self._branches(clone))

    def test_the_push_goes_to_the_named_branch(self) -> None:
        task_id, clone = self._prepared()
        self._commit_work(clone)

        result = self.publisher.push_task(task_id)

        self.assertEqual(result.get('branch'), BRANCH, result)
        self.assertEqual(result.get('pushed_repositories'), ['api'], result)
        on_remote = _git(self.remote, 'branch', '--format=%(refname:short)').split()
        self.assertIn(BRANCH, on_remote)
        self.assertNotIn(task_id, on_remote)

    def test_the_push_access_recheck_asks_about_the_named_branch(self) -> None:
        task_id, _clone = self._prepared()

        with mock.patch.object(
            self.repositories, 'is_branch_pushable', return_value=True,
        ) as pushable:
            self.publisher.recheck_repository_push_access(task_id, 'api')

        self.assertEqual(pushable.call_args.args[1], BRANCH)

    # ----- names that are refused, before anything exists -----

    def _assert_refused(self, branch: str, words: str) -> None:
        tasks_before = len(self.store.list())
        with self.assertRaises(LocalTaskRejected) as caught:
            self._create(branch)
        self.assertIn(words, str(caught.exception))
        self.assertEqual(len(self.store.list()), tasks_before)
        self.assertIsNone(self.workspace.get(f'LOCAL-{tasks_before + 1}'))

    def test_a_name_git_would_refuse_is_refused_with_a_reason(self) -> None:
        self._assert_refused('has space', 'letters, digits')
        self._assert_refused('-rf', 'start with a letter')

    def test_the_branch_a_repository_merges_into_is_refused(self) -> None:
        # Worked on directly, the agent's commits land on it with no pull request.
        self._assert_refused('master', 'merges into')
        self._assert_refused('MASTER', 'merges into')

    def test_a_branch_another_task_works_on_is_refused(self) -> None:
        self._create()

        self._assert_refused(BRANCH, 'LOCAL-1 already works on')
        self._assert_refused(BRANCH.upper(), 'LOCAL-1 already works on')

    def test_a_branch_named_after_another_tasks_id_is_refused(self) -> None:
        self._create('')

        self._assert_refused('LOCAL-1', 'LOCAL-1 already works on')

    def test_a_repository_with_no_known_destination_is_said_so(self) -> None:
        with mock.patch.object(
            self.repositories, 'destination_branch',
            side_effect=ValueError('unable to determine destination branch for repository api'),
        ):
            self._assert_refused(BRANCH, 'unable to determine destination branch')


if __name__ == '__main__':
    unittest.main()
