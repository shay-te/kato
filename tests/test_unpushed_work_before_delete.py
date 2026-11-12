"""Before a task is deleted, the page asks what its workspace would lose.

``TaskPublishService.task_unpushed_work`` reads every clone of the task with
real git: uncommitted changes and commits pushed nowhere. A clone git cannot
read is "could not check", never "nothing to lose". The route hands the answer
to the page, which warns before the delete dialog opens.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from git_core_lib.git_core_lib.client.git_client import GitClientMixin
from kato_core_lib.data_layers.service import task_publish_service
from kato_core_lib.data_layers.service.agent_service import AgentService
from kato_webserver.app import create_app


class _GitOnlyRepositories(GitClientMixin):
    """The repository service's git half: the real ``work_not_on_remote``."""

    def __init__(self) -> None:
        self.logger = logging.getLogger('test.unpushed_work')


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ['git', *args], cwd=str(cwd), check=True, capture_output=True, text=True,
    ).stdout.strip()


def _service(repository_service) -> AgentService:
    return AgentService(
        task_service=MagicMock(),
        task_state_service=MagicMock(),
        implementation_service=MagicMock(),
        testing_service=MagicMock(),
        repository_service=repository_service,
        notification_service=MagicMock(),
    )


class TaskUnpushedWorkTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.clones = {name: self._clone(name) for name in ('backend', 'client')}
        self.service = _service(_GitOnlyRepositories())

    def _clone(self, name: str) -> Path:
        remote = self.root / f'{name}.git'
        _git(self.root, 'init', '-q', '--bare', '-b', 'master', str(remote))
        clone = self.root / 'T-1' / name
        clone.parent.mkdir(exist_ok=True)
        _git(self.root, 'clone', '-q', str(remote), str(clone))
        _git(clone, 'config', 'user.email', 't@example.com')
        _git(clone, 'config', 'user.name', 'test')
        _git(clone, 'commit', '-q', '--allow-empty', '-m', 'base')
        _git(clone, 'push', '-q', '-u', 'origin', 'master')
        _git(clone, 'checkout', '-q', '-b', 'T-1')
        return clone

    def _work(self, repositories=None) -> dict:
        repos = repositories if repositories is not None else [
            SimpleNamespace(id=name, local_path=str(path))
            for name, path in self.clones.items()
        ]
        with patch.object(self.service.publish, '_resolve_publish_context',
                          return_value=(repos, 'T-1', SimpleNamespace(id='T-1'))):
            return self.service.publish.task_unpushed_work('T-1')

    def test_a_task_with_nothing_new_has_nothing_to_lose(self) -> None:
        self.assertEqual(self._work(), {'repositories': [], 'unchecked': []})

    def test_names_each_repository_and_what_it_holds(self) -> None:
        (self.clones['backend'] / 'a.py').write_text('x = 1\n')
        (self.clones['backend'] / 'b.py').write_text('y = 2\n')
        _git(self.clones['client'], 'commit', '-q', '--allow-empty', '-m', 'work')
        self.assertEqual(self._work(), {
            'repositories': [
                {'repository_id': 'backend', 'changed_files': 2, 'unpushed_commits': 0},
                {'repository_id': 'client', 'changed_files': 0, 'unpushed_commits': 1},
            ],
            'unchecked': [],
        })

    def test_pushed_work_is_not_counted(self) -> None:
        _git(self.clones['client'], 'commit', '-q', '--allow-empty', '-m', 'work')
        _git(self.clones['client'], 'push', '-q', '-u', 'origin', 'T-1')
        self.assertEqual(self._work()['repositories'], [])

    def test_a_repository_with_no_clone_on_disk_is_left_out(self) -> None:
        repos = [SimpleNamespace(id='gone', local_path=str(self.root / 'T-1' / 'gone'))]
        self.assertEqual(self._work(repos), {'repositories': [], 'unchecked': []})

    def test_a_clone_git_cannot_read_is_unchecked_never_clean(self) -> None:
        broken = self.root / 'T-1' / 'broken'
        (broken / '.git').mkdir(parents=True)
        repos = [SimpleNamespace(id='broken', local_path=str(broken))]
        result = self._work(repos)
        self.assertEqual(result['repositories'], [])
        self.assertEqual([entry['repository_id'] for entry in result['unchecked']], ['broken'])
        self.assertTrue(result['unchecked'][0]['error'])

    def test_a_clone_that_outruns_the_check_is_unchecked(self) -> None:
        repository_service = MagicMock()
        repository_service.work_not_on_remote.side_effect = lambda path: time.sleep(1)
        service = _service(repository_service)
        repos = [SimpleNamespace(id='slow', local_path=str(self.clones['backend']))]
        with patch.object(task_publish_service, 'UNPUSHED_WORK_CHECK_SECONDS', 0.05), \
                patch.object(service.publish, '_resolve_publish_context',
                             return_value=(repos, 'T-1', SimpleNamespace(id='T-1'))):
            result = service.publish.task_unpushed_work('T-1')
        self.assertEqual(result['repositories'], [])
        self.assertEqual(result['unchecked'][0]['repository_id'], 'slow')
        self.assertIn('did not answer', result['unchecked'][0]['error'])

    def test_no_workspace_or_no_task_id_has_nothing_to_lose(self) -> None:
        self.assertEqual(self.service.publish.task_unpushed_work(''),
                         {'repositories': [], 'unchecked': []})
        self.assertEqual(self._work([]), {'repositories': [], 'unchecked': []})


class UnpushedWorkRouteTests(unittest.TestCase):
    def test_hands_the_service_answer_to_the_page(self) -> None:
        answer = {'repositories': [{'repository_id': 'backend', 'changed_files': 1,
                                    'unpushed_commits': 0}], 'unchecked': []}
        agent = SimpleNamespace(publish=SimpleNamespace(task_unpushed_work=lambda task_id: dict(answer)))
        app = create_app(session_manager=MagicMock(), agent_service=agent)
        payload = app.test_client().get('/api/sessions/T-7/unpushed-work').get_json()
        self.assertEqual(payload['repositories'], answer['repositories'])
        self.assertEqual(payload['task_id'], 'T-7')

    def test_no_service_to_ask_is_could_not_check_never_clean(self) -> None:
        app = create_app(session_manager=MagicMock())
        response = app.test_client().get('/api/sessions/T-1/unpushed-work')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['repositories'], [])
        self.assertEqual(len(payload['unchecked']), 1)


if __name__ == '__main__':
    unittest.main()
