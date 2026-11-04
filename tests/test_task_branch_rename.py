"""A task's branch can be renamed in every clone, and on the remote where it was pushed.

Refused where a rename would lose or break something: commits only the remote
has, an open pull request, a remote or a pull request API that cannot be asked.
Refused, it changes nothing, here or on the remote. Real git repositories and
real services; only the pull request API is stood in for (the remote here is a
folder, which has none).
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
        patcher = mock.patch.object(self.repositories, 'pull_request_states', return_value=[])
        self.pull_requests = patcher.start()
        self.addCleanup(patcher.stop)

    def _current(self, clone: Path) -> str:
        return _git(clone, 'branch', '--show-current').strip()

    def _on_remote(self, remote: Path | None = None) -> dict[str, str]:
        lines = _git(remote or self.remote, 'for-each-ref', '--format=%(refname:short) %(objectname)', 'refs/heads')
        return dict(line.split() for line in lines.splitlines())

    def _pushed(self, clone: Path, branch: str = 'feature/old') -> str:
        _git(clone, 'push', '-q', '-u', 'origin', branch)
        return _git(clone, 'rev-parse', branch)

    def assert_refused(self, task_id: str, clone: Path, words: str, name: str = NEW_NAME) -> None:
        before = self._current(clone), list(self.workspace.get(task_id).tags), self._on_remote()
        with self.assertRaises(BranchRenameRefused) as refused:
            self.renames.rename_task_branch(task_id, name)
        self.assertIn(words, str(refused.exception))
        after = self._current(clone), list(self.workspace.get(task_id).tags), self._on_remote()
        self.assertEqual(after, before)


class RenameTests(_Rename):
    def test_the_clone_moves_to_the_new_name(self) -> None:
        task_id, clone = self._prepared('feature/old')
        result = self.renames.rename_task_branch(task_id, NEW_NAME)
        self.assertEqual(self._current(clone), NEW_NAME)
        self.assertNotIn('feature/old', self._branches(clone))
        self.assertEqual(result, {
            'branch': NEW_NAME, 'renamed': ['api'], 'renamed_on_remote': [], 'problems': [],
        })

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
        self.assertIn(NEW_NAME, self._on_remote())
        self.assertNotIn('feature/old', self._on_remote())

    def test_the_same_name_changes_nothing(self) -> None:
        task_id, clone = self._prepared('feature/old')
        result = self.renames.rename_task_branch(task_id, 'feature/old')
        self.assertEqual(result, {
            'branch': 'feature/old', 'renamed': [], 'renamed_on_remote': [], 'problems': [],
        })
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


class RemoteRenameTests(_Rename):
    def test_a_pushed_branch_is_renamed_on_the_remote_too(self) -> None:
        task_id, clone = self._prepared('feature/old')
        self._commit_work(clone)
        commit = self._pushed(clone)
        result = self.renames.rename_task_branch(task_id, NEW_NAME)
        self.assertEqual(self._on_remote().get(NEW_NAME), commit)
        self.assertNotIn('feature/old', self._on_remote())
        self.assertEqual(result['renamed_on_remote'], ['api'])
        self.assertEqual(result['problems'], [])

    def test_commits_never_pushed_stay_here(self) -> None:
        # Only what the remote already holds moves: a rename is not a push.
        task_id, clone = self._prepared('feature/old')
        pushed = self._pushed(clone)
        self._commit_work(clone)
        local = _git(clone, 'rev-parse', 'HEAD')
        self.renames.rename_task_branch(task_id, NEW_NAME)
        self.assertEqual(self._on_remote()[NEW_NAME], pushed)
        self.assertEqual(_git(clone, 'rev-parse', 'HEAD'), local)

    def test_the_clone_tracks_the_new_remote_branch(self) -> None:
        task_id, clone = self._prepared('feature/old')
        self._pushed(clone)
        self.renames.rename_task_branch(task_id, NEW_NAME)
        self.assertEqual(
            _git(clone, 'rev-parse', '--abbrev-ref', f'{NEW_NAME}@{{upstream}}'), f'origin/{NEW_NAME}',
        )
        self.assertNotIn('origin/feature/old', _git(clone, 'branch', '-r', '--format=%(refname:short)').split())

    def test_a_later_push_lands_on_the_new_remote_branch(self) -> None:
        task_id, clone = self._prepared('feature/old')
        self._pushed(clone)
        self.renames.rename_task_branch(task_id, NEW_NAME)
        self._commit_work(clone)
        self.repositories._push_branch(str(clone), NEW_NAME, self.repositories.get_repository('api'))
        self.assertEqual(self._on_remote()[NEW_NAME], _git(clone, 'rev-parse', 'HEAD'))
        self.assertNotIn('feature/old', self._on_remote())

    def test_it_asks_about_pull_requests_from_the_old_branch(self) -> None:
        task_id, clone = self._prepared('feature/old')
        self._pushed(clone)
        self.renames.rename_task_branch(task_id, NEW_NAME)
        self.assertEqual(self.pull_requests.call_args.kwargs, {'source_branch': 'feature/old'})

    def test_a_merged_or_declined_pull_request_does_not_stop_it(self) -> None:
        task_id, clone = self._prepared('feature/old')
        self._pushed(clone)
        self.pull_requests.return_value = [{'state': 'merged'}, {'state': 'declined'}]
        self.renames.rename_task_branch(task_id, NEW_NAME)
        self.assertIn(NEW_NAME, self._on_remote())

    def test_a_branch_pushed_and_deleted_there_is_renamed_here_only(self) -> None:
        task_id, clone = self._prepared('feature/old')
        self._pushed(clone)
        _git(self.remote, 'branch', '-qD', 'feature/old')
        result = self.renames.rename_task_branch(task_id, NEW_NAME)
        self.assertEqual((result['renamed'], result['renamed_on_remote']), (['api'], []))
        self.assertNotIn(NEW_NAME, self._on_remote())

    def test_a_branch_never_pushed_asks_no_pull_request_api(self) -> None:
        task_id, _clone = self._prepared('feature/old')
        self.renames.rename_task_branch(task_id, NEW_NAME)
        self.pull_requests.assert_not_called()


class RefusedTests(_Rename):
    def test_an_open_pull_request_stops_it(self) -> None:
        task_id, clone = self._prepared('feature/old')
        self._pushed(clone)
        self.pull_requests.return_value = [{'state': 'open', 'url': 'https://git.example/pr/7'}]
        self.assert_refused(task_id, clone, 'a pull request is open from feature/old on api (https://git.example/pr/7)')

    def test_a_pull_request_api_that_cannot_be_asked_is_not_a_yes(self) -> None:
        task_id, clone = self._prepared('feature/old')
        self._pushed(clone)
        self.pull_requests.side_effect = RuntimeError('no token')
        self.assert_refused(task_id, clone, 'could not check api for a pull request from feature/old')

    def test_commits_only_the_remote_has_stop_it(self) -> None:
        task_id, clone = self._prepared('feature/old')
        self._pushed(clone)
        _git(self.seed, 'fetch', '-q', 'origin')
        _git(self.seed, 'checkout', '-q', '-b', 'theirs', 'origin/feature/old')
        (self.seed / 'theirs.py').write_text('THEIRS = 1\n', encoding='utf-8')
        _git(self.seed, 'add', '-A')
        _git(self.seed, 'commit', '-qm', 'theirs')
        _git(self.seed, 'push', '-q', 'origin', 'theirs:feature/old')
        self.assert_refused(task_id, clone, 'has commits this clone does not have')

    def test_a_branch_only_on_the_remote_is_refused(self) -> None:
        task_id, clone = self._prepared('feature/old')
        self._pushed(clone)
        _git(clone, 'checkout', '-q', 'master')
        _git(clone, 'branch', '-qD', 'feature/old')
        self.assert_refused(task_id, clone, 'not in this clone')

    def test_a_remote_that_cannot_be_asked_is_not_a_yes(self) -> None:
        task_id, clone = self._prepared('feature/old')
        with mock.patch.object(
            self.repositories, 'remote_branch_commit', side_effect=RuntimeError('offline'),
        ):
            self.assert_refused(task_id, clone, 'could not ask the remote of api')

    def test_an_answer_that_is_not_a_commit_is_refused(self) -> None:
        task_id, clone = self._prepared('feature/old')
        with mock.patch.object(self.repositories, 'remote_branch_commit', return_value='--upload-pack=x'):
            self.assert_refused(task_id, clone, 'unexpected commit')

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

    def test_the_task_a_live_plan_was_split_from_keeps_its_branch(self) -> None:
        # Its sub-tasks' pull requests go into this branch: renaming it would
        # delete the branch they are open against.
        task_id, clone = self._prepared('feature/old')
        live = {task_id.lower()}
        self.renames = TaskBranchRenameService(
            self.repositories, self.workspace, self.sessions, live_plan_sources=live,
        )
        self.assert_refused(task_id, clone, "sub-tasks' pull requests go into this branch")
        live.clear()  # the plan is done or stopped
        self.renames.rename_task_branch(task_id, NEW_NAME)
        self.assertEqual(self._current(clone), NEW_NAME)

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
        self.web_remote = self.root / 'remote' / 'web.git'
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

    def _both_pushed(self) -> dict:
        for clone in self.clones:
            self._pushed(clone)
        return {'api': self._on_remote(), 'web': self._on_remote(self.web_remote)}

    def _fail_push_on(self, failing_path: Path):
        real = self.repositories.push_commit_as_new_branch

        def push(path, commit, branch, repository=None):
            if path == str(failing_path):
                raise RuntimeError('remote said no')
            real(path, commit, branch, repository)

        return mock.patch.object(self.repositories, 'push_commit_as_new_branch', side_effect=push)

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
        self.assertIn('api is still on feature/renamed', str(refused.exception))

    def test_a_push_that_fails_leaves_every_remote_as_it_was(self) -> None:
        before = self._both_pushed()
        with self._fail_push_on(self.clones[1]), self.assertRaises(BranchRenameRefused) as refused:
            self.renames.rename_task_branch(self.task_id, NEW_NAME)
        self.assertIn('every repository was put back', str(refused.exception))
        self.assertEqual({'api': self._on_remote(), 'web': self._on_remote(self.web_remote)}, before)
        self.assertEqual([self._current(clone) for clone in self.clones], ['feature/old'] * 2)
        self.assertEqual(self.repositories.task_branch_name(self.task_id), 'feature/old')

    def test_a_new_remote_branch_that_cannot_be_taken_back_is_named(self) -> None:
        self._both_pushed()
        with self._fail_push_on(self.clones[1]), mock.patch.object(
            self.repositories, 'delete_remote_branch', side_effect=RuntimeError('protected'),
        ), self.assertLogs('kato_core_lib', level='ERROR'):
            with self.assertRaises(BranchRenameRefused) as refused:
                self.renames.rename_task_branch(self.task_id, NEW_NAME)
        self.assertIn(f'{NEW_NAME} is still on the remote of api', str(refused.exception))

    def test_an_old_branch_that_cannot_be_deleted_is_reported(self) -> None:
        self._both_pushed()
        with mock.patch.object(
            self.repositories, 'delete_remote_branch', side_effect=RuntimeError('protected'),
        ), self.assertLogs('kato_core_lib', level='ERROR'):
            result = self.renames.rename_task_branch(self.task_id, NEW_NAME)
        self.assertEqual(len(result['problems']), 2)
        self.assertIn('feature/old is still on the remote of api: protected', result['problems'][0])
        self.assertIn('delete feature/old there by hand', result['problems'][0])
        # The rename stands: the work is safe under the new name.
        self.assertEqual(self.repositories.task_branch_name(self.task_id), NEW_NAME)
        self.assertEqual(set(self._on_remote()), {'master', 'feature/old', NEW_NAME})

    def test_tracking_that_cannot_be_set_is_reported(self) -> None:
        self._both_pushed()
        with mock.patch.object(
            self.repositories, 'set_upstream_branch', side_effect=RuntimeError('locked'),
        ), self.assertLogs('kato_core_lib', level='ERROR'):
            result = self.renames.rename_task_branch(self.task_id, NEW_NAME)
        self.assertIn(f'api does not track origin/{NEW_NAME} yet: locked', result['problems'][0])
        self.assertNotIn('feature/old', self._on_remote())


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
        self._pushed(clone)
        self.pull_requests.return_value = [{'state': 'open', 'url': ''}]
        response = self._post(task_id, {'branch': NEW_NAME})
        self.assertEqual(response.status_code, 409)
        self.assertIn('a pull request is open', response.get_json()['error'])

    def test_problems_reach_the_page(self) -> None:
        task_id, clone = self._prepared('feature/old')
        self._pushed(clone)
        with mock.patch.object(
            self.repositories, 'delete_remote_branch', side_effect=RuntimeError('protected'),
        ), self.assertLogs('kato_core_lib', level='ERROR'):
            response = self._post(task_id, {'branch': NEW_NAME})
        self.assertEqual(response.status_code, 200)
        self.assertIn('still on the remote of api', response.get_json()['problems'][0])

    def test_no_name(self) -> None:
        self.assertEqual(self._post('T-1', {}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
