"""The Git provider tab's "Test connection".

A git host is asked for a few of the repositories kato knows there, with the
very calls kato makes before it opens a pull request; its own words come back
per repository. The settings tested are the form's, before Save.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from kato_core_lib.validation import git_host_connection_check as check


class FakeRepositoryService(object):
    """The four calls the check makes, on repositories named by host."""

    def __init__(self, repositories, *, refused=(), unplaced=(), listing_error=None):
        self.repositories = repositories
        self.refused = dict(refused)
        self.unplaced = set(unplaced)
        self.listing_error = listing_error
        self.prepared = []

    def _ensure_repositories(self):
        if self.listing_error:
            raise self.listing_error
        return self.repositories

    def _resolved_pull_request_provider(self, repository):
        if repository.id in self.unplaced:
            raise ValueError('unable to determine pull request provider')
        return repository.host

    def _prepare_pull_request_api(self, repository):
        self.prepared.append(repository.id)
        if repository.id in self.refused:
            raise self.refused[repository.id]

    def _pull_request_data_access(self, repository):
        return SimpleNamespace(validate_connection=Mock())


def repo(name, host):
    return SimpleNamespace(id=name, host=host)


class HostAnswerTests(unittest.TestCase):
    def test_the_host_answers_for_its_repositories(self) -> None:
        service = FakeRepositoryService([repo('api', 'bitbucket'), repo('site', 'github'), repo('web', 'bitbucket')])
        answer = check.host_answer(service, 'bitbucket')
        self.assertEqual(answer['ok'], True)
        self.assertEqual(answer['message'], 'Connected — Bitbucket answered for api, web.')
        self.assertEqual(sorted(service.prepared), ['api', 'web'])  # never another host's

    def test_each_refusal_is_named_with_the_hosts_own_words(self) -> None:
        service = FakeRepositoryService(
            [repo('api', 'bitbucket'), repo('web', 'bitbucket')],
            refused={'web': RuntimeError('401 Unauthorized: the app password was refused'), 'api': ValueError('')},
        )
        answer = check.host_answer(service, 'bitbucket')
        self.assertFalse(answer['ok'])
        self.assertEqual(answer['error'], 'api: ValueError\nweb: 401 Unauthorized: the app password was refused')
        self.assertEqual(answer['message'], '')

    def test_at_most_a_few_repositories_are_asked(self) -> None:
        service = FakeRepositoryService([repo(f'r{n}', 'gitlab') for n in range(8)])
        answer = check.host_answer(service, 'gitlab')
        self.assertEqual(len(answer['checked']), check.MAX_REPOSITORIES)

    def test_nothing_on_that_host_or_no_list_says_so(self) -> None:
        none_there = check.host_answer(FakeRepositoryService([repo('a', 'github')], unplaced={'a'}), 'azure')
        self.assertFalse(none_there['ok'])
        self.assertIn('kato found no repository on Azure DevOps', none_there['error'])
        no_list = check.host_answer(FakeRepositoryService([], listing_error=RuntimeError('no such folder')), 'github')
        self.assertEqual(no_list['error'], 'kato could not list your repositories: no such folder')


class ChildProcessTests(unittest.TestCase):
    def test_the_answer_is_the_childs_last_json_line(self) -> None:
        answer = {'ok': True, 'host': 'github', 'error': '', 'message': 'Connected', 'checked': []}
        done = subprocess.CompletedProcess([], 0, stdout='noise\n' + json.dumps(answer) + '\n', stderr='')
        with patch.object(check.subprocess, 'run', return_value=done) as run:
            self.assertEqual(check.check_git_host_connection({'A': 'b'}, 'github'), answer)
        self.assertEqual(run.call_args.args[0][-2:], [check.__name__, 'github'])
        self.assertEqual(run.call_args.kwargs['env'], {'A': 'b'})

    def test_a_child_that_dies_or_hangs_says_why(self) -> None:
        died = subprocess.CompletedProcess([], 1, stdout='not json\n', stderr='Traceback\nKeyError: x\n')
        with patch.object(check.subprocess, 'run', return_value=died):
            self.assertEqual(check.check_git_host_connection({}, 'github')['error'], 'Traceback\nKeyError: x')
        silent = subprocess.CompletedProcess([], 3, stdout='', stderr='')
        with patch.object(check.subprocess, 'run', return_value=silent):
            self.assertEqual(check.check_git_host_connection({}, 'github')['error'], 'the check stopped with exit code 3')
        with patch.object(check.subprocess, 'run', side_effect=subprocess.TimeoutExpired('x', 90)):
            self.assertIn('no answer within 90 seconds', check.check_git_host_connection({}, 'github')['error'])

    def test_a_real_child_builds_kato_and_answers(self) -> None:
        """For real: kato's config, the repository walk and the answer line —
        a folder with one GitHub repository, asked about GitLab (no network)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'code'
            (root / 'api').mkdir(parents=True)
            subprocess.run(['git', 'init', '-q', str(root / 'api')], check=True)
            subprocess.run(['git', '-C', str(root / 'api'), 'remote', 'add', 'origin',
                            'https://github.com/someone/api.git'], check=True)
            env = {
                'PATH': os.environ.get('PATH', ''), 'HOME': tmp,
                'KATO_SETTINGS_FILE': str(Path(tmp) / 'settings.json'),
                'REPOSITORY_ROOT_PATH': str(root), 'KATO_ISSUE_PLATFORM': 'local',
                'KATO_AGENT_BACKEND': 'claude', 'KATO_WORKSPACES_ROOT': str(Path(tmp) / 'ws'),
            }
            answer = check.check_git_host_connection(env, 'gitlab', timeout=120)
        self.assertFalse(answer['ok'], answer)
        self.assertIn('kato found no repository on GitLab', answer['error'])


class RouteTests(unittest.TestCase):
    def post(self, body):
        from kato_webserver.app import create_app

        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {'KATO_SETTINGS_FILE': os.path.join(tmp, 'settings.json')}, clear=True), \
                patch.object(check, 'check_git_host_connection', return_value={'ok': True}) as run:
            app = create_app(session_manager=Mock(), needs_config=True)
            response = app.test_client().post('/api/git-providers/test', json=body)
        return response, run

    def test_the_form_is_tested_for_that_host_only(self) -> None:
        response, run = self.post({'provider': 'bitbucket', 'fields': {
            'BITBUCKET_API_TOKEN': 'typed', 'GITHUB_API_TOKEN': 'not this host', 'PATH': '/evil',
        }})
        self.assertEqual((response.status_code, response.get_json()), (200, {'ok': True}))
        env, host = run.call_args.args
        self.assertEqual((host, env['BITBUCKET_API_TOKEN']), ('bitbucket', 'typed'))
        self.assertNotIn('GITHUB_API_TOKEN', env)
        self.assertNotEqual(env.get('PATH'), '/evil')

    def test_an_unknown_host_or_bad_fields_is_refused(self) -> None:
        for body in ({'provider': 'gitea'}, {}, {'provider': 'github', 'fields': ['x']}):
            with self.subTest(body=body):
                response, run = self.post(body)
                self.assertEqual(response.status_code, 400)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
