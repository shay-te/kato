"""A repository tagged on a plan's sub-task reaches its workspace.

Operator report: "he is not pulling the repos I added to the subtask". A sub-task
names its repositories in the footer of its description, and resolution stopped
there, so a ``kato:repo:`` tag put on the ticket in the tracker never cloned
anything. The footer is now where a sub-task starts, and tags add to it.

Real git repositories, real clones, the real RepositoryService, WorkspaceService
and TaskRepositoryService. Only the tracker is a stub: the scan hands the task
in, tags and all, exactly as the tracker returned it.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib.data_layers.service.repository_service import RepositoryService
from kato_core_lib.data_layers.service.task_repository_service import TaskRepositoryService
from kato_core_lib.data_layers.service.workspace_provisioning_service import (
    provision_task_workspace_clones,
)
from tests.chaos_lib import build_real_workspace_service
from tests.test_plan_queue_kato_hooks import footer
from tests.utils import build_task

TASK_ID = 'UNA-3272'


def _git(cwd, *args) -> str:
    return subprocess.run(
        ['git', *args], cwd=str(cwd), check=True, capture_output=True, text=True,
    ).stdout.strip()


class SubTaskRepositoryTagTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-sub-task-tag-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.source_root = self.root / 'source'
        self.source_root.mkdir()
        self.backend = self._repository('backend', 'api.py')
        self.frontend = self._repository('frontend', 'page.py')
        self.workspaces = build_real_workspace_service(self.root / 'workspaces')
        self.repositories = RepositoryService([self.backend, self.frontend], 3)
        # Pull-request credentials need a live provider, and no git step here uses them.
        self.repositories._prepare_repository_access = lambda repository: repository
        self.service = TaskRepositoryService(
            repository_service=self.repositories,
            task_service=MagicMock(),
            workspace_manager=self.workspaces,
            logger=MagicMock(),
        )
        env = patch.dict('os.environ', {
            'REPOSITORY_ROOT_PATH': str(self.source_root),
            'KATO_WORKSPACES_ROOT': str(self.root / 'workspaces'),
        })
        env.start()
        self.addCleanup(env.stop)

    def _repository(self, name: str, code_file: str) -> SimpleNamespace:
        remote = self.root / 'remote' / name
        remote.mkdir(parents=True)
        _git(remote, 'init', '-q', '-b', 'master')
        _git(remote, 'config', 'user.email', 't@example.com')
        _git(remote, 'config', 'user.name', 'test')
        (remote / code_file).write_text('VALUE = 1\n', encoding='utf-8')
        _git(remote, 'add', '-A')
        _git(remote, 'commit', '-qm', 'initial')
        _git(self.source_root, 'clone', '-q', str(remote), name)
        return SimpleNamespace(
            id=name, display_name=name, repo_slug=name, aliases=[],
            local_path=str(self.source_root / name), remote_url=str(remote),
            destination_branch='master',
        )

    def _sub_task(self, tags=()):
        return build_task(task_id=TASK_ID, description=footer(('backend',)), tags=list(tags))

    def _start_sub_task(self) -> None:
        """Pickup: resolve, clone, prepare the task branch."""
        task = self._sub_task()
        repositories = self.repositories.resolve_task_repositories(task)
        provisioned = provision_task_workspace_clones(
            self.workspaces, self.repositories, task, repositories,
        )
        self.repositories.prepare_task_branches(
            provisioned, {repository.id: TASK_ID for repository in provisioned},
        )

    def _clone(self, repository_id: str) -> Path:
        return Path(self.workspaces.repository_path(TASK_ID, repository_id))

    def test_a_sub_task_starts_in_its_footers_repositories_only(self) -> None:
        self._start_sub_task()

        self.assertTrue((self._clone('backend') / 'api.py').is_file())
        self.assertFalse(self._clone('frontend').exists())

    def test_a_repository_tagged_on_a_running_sub_task_is_cloned_on_the_next_scan(self) -> None:
        self._start_sub_task()

        result = self.service.reconcile_task_repositories(
            TASK_ID, task=self._sub_task(['kato:repo:frontend']),
        )

        self.assertEqual(result.get('added_repositories'), ['frontend'], result)
        clone = self._clone('frontend')
        self.assertTrue((clone / 'page.py').is_file())
        self.assertEqual(_git(clone, 'rev-parse', '--abbrev-ref', 'HEAD'), TASK_ID)
        self.assertEqual(
            sorted(self.workspaces.get(TASK_ID).repository_ids), ['backend', 'frontend'],
        )

    def test_a_repository_tagged_before_pickup_is_cloned_at_pickup(self) -> None:
        task = self._sub_task(['kato:repo:frontend'])

        repositories = self.repositories.resolve_task_repositories(task)
        provision_task_workspace_clones(self.workspaces, self.repositories, task, repositories)

        self.assertTrue((self._clone('backend') / 'api.py').is_file())
        self.assertTrue((self._clone('frontend') / 'page.py').is_file())

    def test_the_operators_own_checkout_gets_no_task_branch(self) -> None:
        self._start_sub_task()

        self.service.reconcile_task_repositories(
            TASK_ID, task=self._sub_task(['kato:repo:frontend']),
        )

        self.assertNotIn(TASK_ID, _git(self.frontend.local_path, 'branch', '--format=%(refname:short)'))

    def test_a_scan_with_no_new_tag_clones_nothing(self) -> None:
        self._start_sub_task()

        self.service.reconcile_task_repositories(TASK_ID, task=self._sub_task())

        self.assertFalse(self._clone('frontend').exists())

    def test_a_tag_this_kato_cannot_match_leaves_the_running_sub_task_alone(self) -> None:
        self._start_sub_task()

        result = self.service.reconcile_task_repositories(
            TASK_ID, task=self._sub_task(['kato:repo:someone-elses-name']),
        )

        self.assertTrue(result.get('synced'), result)
        self.assertEqual(self.workspaces.get(TASK_ID).repository_ids, ['backend'])


if __name__ == '__main__':
    unittest.main()
