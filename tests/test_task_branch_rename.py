"""A task's branch can be renamed while its name is still only on this machine.

Once it reaches a remote, the name is in other clones and in any pull request,
so the rename is refused, and it changes nothing when it is. Real git
repositories and real services.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.plan_queue.step_footer import subtask_description
from kato_core_lib.data_layers.service.task_branch_rename_service import (
    BranchRenameRefused,
    TaskBranchRenameService,
)
from kato_core_lib.helpers.kato_tag_utils import build_branch_tag
from kato_webserver.app import create_app
from plan_queue_core_lib.plan_queue_core_lib.ports import LaunchRequest
from tests.test_local_task_branch_name import RealLocalTask, _git

NEW_NAME = 'feature/renamed'


class _Rename(RealLocalTask, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sessions = mock.MagicMock()
        self.sessions.get_session.return_value = None
        self.renames = TaskBranchRenameService(self.repositories, self.workspace, self.sessions)

    def _current(self, clone: Path) -> str:
        return _git(clone, 'branch', '--show-current').strip()

    def assert_refused(self, task_id: str, clone: Path, words: str, name: str = NEW_NAME) -> None:
        before = self._current(clone), list(self.workspace.get(task_id).tags)
        with self.assertRaises(BranchRenameRefused) as refused:
            self.renames.rename_task_branch(task_id, name)
        self.assertIn(words, str(refused.exception))
        self.assertEqual((self._current(clone), list(self.workspace.get(task_id).tags)), before)


class RenameTests(_Rename):
    def test_the_clone_moves_to_the_new_name(self) -> None:
        task_id, clone = self._prepared('feature/old')
        result = self.renames.rename_task_branch(task_id, NEW_NAME)
        self.assertEqual(self._current(clone), NEW_NAME)
        self.assertNotIn('feature/old', self._branches(clone))
        self.assertEqual(result, {'branch': NEW_NAME, 'renamed': ['api']})

    def test_every_later_step_asks_and_gets_the_new_name(self) -> None:
        task_id, _clone = self._prepared('feature/old')
        self.renames.rename_task_branch(task_id, NEW_NAME)
        self.assertEqual(self.repositories.task_branch_name(task_id), NEW_NAME)

    def test_the_old_name_leaves_kato_s_tags(self) -> None:
        task_id, _clone = self._prepared('feature/old')
        self.renames.rename_task_branch(task_id, NEW_NAME)
        tags = self.workspace.get(task_id).tags
        self.assertNotIn(build_branch_tag('feature/old'), tags)
        self.assertIn(build_branch_tag(NEW_NAME), tags)

    def test_a_task_on_its_id_can_be_named(self) -> None:
        task_id, clone = self._prepared('')
        self.renames.rename_task_branch(task_id, NEW_NAME)
        self.assertEqual(self._current(clone), NEW_NAME)
        self.assertEqual(self.repositories.task_branch_name(task_id), NEW_NAME)

    def test_commits_and_unsaved_work_stay_as_they_are(self) -> None:
        task_id, clone = self._prepared('feature/old')
        self._commit_work(clone)
        commit = _git(clone, 'rev-parse', 'HEAD')
        (clone / 'draft.py').write_text('DRAFT = 1\n', encoding='utf-8')
        self.renames.rename_task_branch(task_id, NEW_NAME)
        self.assertEqual(_git(clone, 'rev-parse', 'HEAD'), commit)
        self.assertTrue((clone / 'draft.py').is_file())

    def test_the_push_goes_to_the_new_name(self) -> None:
        task_id, clone = self._prepared('feature/old')
        self._commit_work(clone)
        self.renames.rename_task_branch(task_id, NEW_NAME)
        repository = self.repositories.get_repository('api')
        self.repositories._push_branch(str(clone), NEW_NAME, repository)
        self.assertIn(NEW_NAME, _git(self.remote, 'branch', '--format=%(refname:short)').split())
        self.assertNotIn('feature/old', _git(self.remote, 'branch', '--format=%(refname:short)').split())

    def test_the_same_name_changes_nothing(self) -> None:
        task_id, clone = self._prepared('feature/old')
        result = self.renames.rename_task_branch(task_id, 'feature/old')
        self.assertEqual(result, {'branch': 'feature/old', 'renamed': []})
        self.assertEqual(self._current(clone), 'feature/old')

    def test_every_repository_of_the_task_moves(self) -> None:
        self._add_repository('web')
        task = self.service.create('Two', 'Do it.', ['api', 'web'], branch='feature/old')
        task_id = str(task.id)
        self.assertTrue(self.service.prepare(task_id))
        self.renames.rename_task_branch(task_id, NEW_NAME)
        for name in ('api', 'web'):
            self.assertEqual(self._current(Path(self.workspace.repository_path(task_id, name))), NEW_NAME)

    def test_a_clone_not_on_the_branch_yet_is_left_alone(self) -> None:
        task_id, clone = self._prepared('feature/old')
        _git(clone, 'checkout', '-q', 'master')
        _git(clone, 'branch', '-qD', 'feature/old')
        self.assertEqual(self.renames.rename_task_branch(task_id, NEW_NAME)['renamed'], [])
        self.assertEqual(self.repositories.task_branch_name(task_id), NEW_NAME)


class RefusedTests(_Rename):
    def test_a_pushed_branch_stays_as_it_is(self) -> None:
        task_id, clone = self._prepared('feature/old')
        _git(clone, 'push', '-q', '-u', 'origin', 'feature/old')
        self.assert_refused(task_id, clone, 'has been pushed')

    def test_a_branch_pushed_and_later_deleted_there_is_still_refused(self) -> None:
        # The pull request it opened can still be open, or merged.
        task_id, clone = self._prepared('feature/old')
        _git(clone, 'push', '-q', '-u', 'origin', 'feature/old')
        _git(self.remote, 'branch', '-qD', 'feature/old')
        self.assert_refused(task_id, clone, 'has been pushed')

    def test_a_branch_someone_else_pushed_is_refused(self) -> None:
        task_id, clone = self._prepared('feature/old')
        _git(self.seed, 'push', '-q', 'origin', 'HEAD:refs/heads/feature/old')
        self.assert_refused(task_id, clone, 'has been pushed')

    def test_a_remote_that_cannot_be_asked_is_not_a_yes(self) -> None:
        task_id, clone = self._prepared('feature/old')
        with mock.patch.object(
            self.repositories, 'remote_has_branch', side_effect=RuntimeError('offline'),
        ):
            self.assert_refused(task_id, clone, 'could not ask the remote of api')

    def test_a_name_already_on_the_remote_is_refused(self) -> None:
        task_id, clone = self._prepared('feature/old')
        _git(self.seed, 'push', '-q', 'origin', f'HEAD:refs/heads/{NEW_NAME}')
        self.assert_refused(task_id, clone, 'already has a branch')

    def test_a_name_already_in_the_clone_is_refused(self) -> None:
        task_id, clone = self._prepared('feature/old')
        _git(clone, 'branch', NEW_NAME)
        self.assert_refused(task_id, clone, 'already has a branch')

    def test_the_branch_it_merges_into_is_refused(self) -> None:
        task_id, clone = self._prepared('feature/old')
        self.assert_refused(task_id, clone, 'merges into', name='master')

    def test_a_name_git_cannot_take_is_refused(self) -> None:
        task_id, clone = self._prepared('feature/old')
        self.assert_refused(task_id, clone, 'cannot be a branch name', name='a b')

    def test_a_branch_another_task_works_on_is_refused(self) -> None:
        task_id, clone = self._prepared('feature/old')
        self._prepared('feature/taken')
        self.assert_refused(task_id, clone, 'already works on', name='feature/taken')

    def test_not_while_the_agent_is_working(self) -> None:
        task_id, clone = self._prepared('feature/old')
        self.sessions.get_session.return_value = SimpleNamespace(is_working=True)
        self.assert_refused(task_id, clone, 'agent is working')

    def test_an_idle_chat_does_not_stop_it(self) -> None:
        task_id, clone = self._prepared('feature/old')
        self.sessions.get_session.return_value = SimpleNamespace(is_working=False)
        self.renames.rename_task_branch(task_id, NEW_NAME)
        self.assertEqual(self._current(clone), NEW_NAME)

    def test_a_plan_step_stays_on_its_ticket_id(self) -> None:
        description = subtask_description(LaunchRequest(
            plan_id='a' * 32, step_id='step-1', index=1, count=2, title='Add',
            description='Do it.', repositories=('api',), source_task_id='UNA-1', plan_title='Plan',
        ), ('api',))
        self._add_task('UNA-9', description)
        clone = Path(self.workspace.repository_path('UNA-9', 'api'))
        self.assert_refused('UNA-9', clone, 'how the plan finds its pull request')

    def test_a_task_with_no_workspace(self) -> None:
        with self.assertRaises(BranchRenameRefused):
            self.renames.rename_task_branch('UNA-404', NEW_NAME)

    def test_a_repository_with_no_clone_yet(self) -> None:
        self.workspace.create(task_id='UNA-5', repository_ids=['api'])
        with self.assertRaises(BranchRenameRefused) as refused:
            self.renames.rename_task_branch('UNA-5', NEW_NAME)
        self.assertIn('no clone', str(refused.exception))

    def _add_task(self, task_id: str, description: str) -> None:
        self.planning.resolve_planning_context(Task(
            id=task_id, summary='Step', description=description, tags=['kato:repo:api'],
        ))


class AllOrNothingTests(_Rename):
    def setUp(self) -> None:
        super().setUp()
        self._add_repository('web')
        task = self.service.create('Two', 'Do it.', ['api', 'web'], branch='feature/old')
        self.task_id = str(task.id)
        self.assertTrue(self.service.prepare(self.task_id))
        self.clones = [
            Path(self.workspace.repository_path(self.task_id, name)) for name in ('api', 'web')
        ]

    def _fail_on(self, failing_path: Path, *, new_name=NEW_NAME):
        real = self.repositories.rename_local_branch

        def rename(path, old, new):
            if path == str(failing_path) and new == new_name:
                raise RuntimeError('disk said no')
            real(path, old, new)

        return mock.patch.object(self.repositories, 'rename_local_branch', side_effect=rename)

    def test_a_repository_that_fails_puts_the_others_back(self) -> None:
        with self._fail_on(self.clones[1]), self.assertRaises(BranchRenameRefused) as refused:
            self.renames.rename_task_branch(self.task_id, NEW_NAME)
        self.assertIn('every repository was put back', str(refused.exception))
        self.assertEqual([self._current(clone) for clone in self.clones], ['feature/old'] * 2)
        self.assertEqual(self.repositories.task_branch_name(self.task_id), 'feature/old')

    def test_a_tag_that_cannot_be_saved_puts_them_back(self) -> None:
        with mock.patch.object(self.workspace, 'replace_tag', side_effect=OSError('full')):
            with self.assertRaises(BranchRenameRefused):
                self.renames.rename_task_branch(self.task_id, NEW_NAME)
        self.assertEqual([self._current(clone) for clone in self.clones], ['feature/old'] * 2)

    def test_a_clone_that_cannot_be_put_back_is_named(self) -> None:
        real = self.repositories.rename_local_branch

        def rename(path, old, new):
            if new == 'feature/old' or path == str(self.clones[1]):
                raise RuntimeError('disk said no')
            real(path, old, new)

        with mock.patch.object(self.repositories, 'rename_local_branch', side_effect=rename):
            with self.assertLogs('kato_core_lib', level='ERROR'):
                with self.assertRaises(BranchRenameRefused) as refused:
                    self.renames.rename_task_branch(self.task_id, NEW_NAME)
        self.assertIn('not put back: api', str(refused.exception))


class RouteTests(_Rename):
    def _post(self, task_id: str, payload):
        agent = SimpleNamespace(branches=self.renames)
        app = create_app(session_manager=mock.MagicMock(), agent_service=agent)
        with app.test_client() as client:
            return client.post(f'/api/sessions/{task_id}/rename-branch', json=payload)

    def test_it_renames(self) -> None:
        task_id, clone = self._prepared('feature/old')
        response = self._post(task_id, {'branch': NEW_NAME})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['branch'], NEW_NAME)
        self.assertEqual(self._current(clone), NEW_NAME)

    def test_a_refusal_carries_its_reason(self) -> None:
        task_id, clone = self._prepared('feature/old')
        _git(clone, 'push', '-q', '-u', 'origin', 'feature/old')
        response = self._post(task_id, {'branch': NEW_NAME})
        self.assertEqual(response.status_code, 409)
        self.assertIn('has been pushed', response.get_json()['error'])

    def test_no_name(self) -> None:
        self.assertEqual(self._post('T-1', {}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
