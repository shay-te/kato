"""Quick start's routes: what the first-run screen reads and what it saves.

The services behind them are tested on real folders and a real child process
(tests/test_quick_start.py); here each route is checked for what it asks them
and what it answers.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask

from kato_webserver import quick_start_routes as routes


class QuickStartRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        self.env = {'REPOSITORY_ROOT_PATH': ''}
        self.written: list[dict] = []
        self.sign_in = mock.Mock()
        patches = [
            mock.patch.object(routes, 'effective_config_env', side_effect=lambda: dict(self.env)),
            mock.patch.object(routes, 'write_kato_settings', side_effect=self.written.append),
            mock.patch.object(routes, 'agent_status', side_effect=lambda agent, env: {
                'agent': agent, 'installed': True, 'version': '2.1.325', 'logged_in': True,
                'binary': f'/b/{agent}', 'install': None,
            }),
            mock.patch.object(routes, 'all_agents_status', side_effect=lambda env: {
                agent: {'agent': agent, 'installed': agent == 'claude'} for agent in ('claude', 'codex', 'grok', 'opencode')
            }),
            mock.patch.object(routes, 'git_status', return_value={'installed': True, 'version': '2.43.0', 'install': None}),
            mock.patch.object(routes.Path, 'home', return_value=self.home),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        app = Flask(__name__)
        routes.register_quick_start_routes(app, sign_in=self.sign_in)
        self.client = app.test_client()

    def test_the_screen_gets_the_checks_and_the_folders_found(self) -> None:
        (self.home / 'dev' / 'api' / '.git').mkdir(parents=True)
        body = self.client.get('/api/setup/quick-start').get_json()
        self.assertEqual(list(body['agents']), ['claude', 'codex', 'grok', 'opencode'])
        self.assertEqual(body['agent'], '')  # nothing picked for the operator
        self.assertTrue(body['git']['installed'])
        self.assertEqual(body['folders'], [{'path': str(self.home / 'dev'), 'repositories': 1}])
        self.assertEqual(body['new_folder'], str(self.home / 'code'))
        self.assertEqual(body['home'], str(self.home))  # the screen shows ~/dev
        self.assertIs(body['folder_dialog'], False)  # no folder dialog registered on this bare app
        self.assertEqual(body['folder'], '')

    def test_kato_own_workspaces_are_never_offered(self) -> None:
        workspaces = self.home / 'kato-ws'
        (workspaces / 'T-1' / '.git').mkdir(parents=True)
        with mock.patch.object(routes, 'kato_home_path', return_value=workspaces):
            body = self.client.get('/api/setup/quick-start').get_json()
        self.assertEqual(body['folders'], [])

    def test_a_pick_made_before_is_offered_again(self) -> None:
        self.env['KATO_CHAT_AGENT'] = 'codex'
        with mock.patch('kato_core_lib.helpers.chat_agent_utils.live_setting',
                        side_effect=lambda key, env=None: self.env.get(key, '')):
            body = self.client.get('/api/setup/quick-start').get_json()
        self.assertEqual(body['agent'], 'codex')

    def test_the_polled_checks_carry_the_picked_agent_and_no_folder_search(self) -> None:
        with mock.patch.object(routes, 'find_code_folders') as search:
            body = self.client.get('/api/setup/quick-start/checks?agent=grok').get_json()
            fallback = self.client.get('/api/setup/quick-start/checks?agent=nonsense').get_json()
        search.assert_not_called()
        self.assertEqual(set(body), {'agent', 'git'})
        self.assertEqual(body['agent']['agent'], 'grok')
        self.assertEqual(fallback['agent']['agent'], 'claude')

    def test_save_writes_the_three_settings_or_says_why_not(self) -> None:
        (self.home / 'dev').mkdir()
        response = self.client.post('/api/setup/quick-start', json={'agent': 'codex', 'folder': str(self.home / 'dev')})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.written, [{
            'KATO_CHAT_AGENT': 'codex', 'KATO_AGENT_BACKEND': 'claude',
            'REPOSITORY_ROOT_PATH': str(self.home / 'dev'), 'KATO_ISSUE_PLATFORM': 'local',
        }])
        no_agent = self.client.post('/api/setup/quick-start', json={'folder': str(self.home / 'dev')})
        self.assertEqual(no_agent.status_code, 400)
        self.assertIn('pick the agent you use', no_agent.get_json()['error'])
        refused = self.client.post('/api/setup/quick-start', json={'agent': 'claude', 'folder': str(self.home / 'code')})
        self.assertEqual(refused.status_code, 400)
        self.assertIn('does not exist', refused.get_json()['error'])
        created = self.client.post('/api/setup/quick-start', json={
            'agent': 'claude', 'folder': str(self.home / 'code'), 'create': True,
        })
        self.assertEqual(created.get_json()['saved']['REPOSITORY_ROOT_PATH'], str(self.home / 'code'))
        # Only a literal true creates a folder.
        self.client.post('/api/setup/quick-start', json={'agent': 'claude', 'folder': str(self.home / 'other'), 'create': 'yes'})
        self.assertFalse((self.home / 'other').exists())

    def test_save_says_what_still_keeps_kato_from_starting(self) -> None:
        (self.home / 'dev').mkdir()
        body = {'agent': 'claude', 'folder': str(self.home / 'dev')}
        with mock.patch('kato_core_lib.validate_env.collect_config_errors',
                        return_value=['missing required agent env var: YOUTRACK_ASSIGNEE']) as check:
            response = self.client.post('/api/setup/quick-start', json=body)
        self.assertEqual(response.get_json()['missing'], ['missing required agent env var: YOUTRACK_ASSIGNEE'])
        self.assertEqual(check.call_args.kwargs['mode'], 'all')  # the check the setup loop waits on
        with mock.patch('kato_core_lib.validate_env.collect_config_errors', return_value=[]):
            self.assertEqual(self.client.post('/api/setup/quick-start', json=body).get_json()['missing'], [])

    def test_a_settings_file_that_cannot_be_written_is_a_500(self) -> None:
        (self.home / 'dev').mkdir()
        with mock.patch.object(routes, 'write_kato_settings', side_effect=PermissionError('read-only')):
            response = self.client.post('/api/setup/quick-start', json={'agent': 'claude', 'folder': str(self.home / 'dev')})
        self.assertEqual(response.status_code, 500)
        self.assertIn('read-only', response.get_json()['error'])

    def test_sign_in_runs_the_configured_cli_and_takes_the_code(self) -> None:
        self.sign_in.start.return_value = {'state': 'waiting'}
        self.sign_in.status.return_value = {'state': 'waiting', 'link': 'https://x'}
        self.sign_in.submit_code.return_value = {'state': 'waiting'}
        self.sign_in.cancel.return_value = {'state': 'cancelled'}
        self.assertEqual(self.client.post('/api/setup/claude-sign-in').get_json(), {'state': 'waiting'})
        self.sign_in.start.assert_called_once_with('/b/claude')
        self.assertEqual(self.client.get('/api/setup/claude-sign-in').get_json()['link'], 'https://x')
        self.client.post('/api/setup/claude-sign-in/code', json={'code': 'abc#123'})
        self.sign_in.submit_code.assert_called_once_with('abc#123')
        self.assertEqual(self.client.post('/api/setup/claude-sign-in/cancel').get_json()['state'], 'cancelled')

    def test_a_refused_code_is_a_400_with_why(self) -> None:
        self.sign_in.submit_code.side_effect = routes.SignInError('paste the code exactly')
        response = self.client.post('/api/setup/claude-sign-in/code', json={})
        self.assertEqual((response.status_code, response.get_json()['error']), (400, 'paste the code exactly'))
        self.sign_in.submit_code.assert_called_once_with('')

    def test_the_git_installer_opens_only_where_kato_can(self) -> None:
        with mock.patch.object(routes, 'start_mac_install', return_value=True):
            self.assertEqual(self.client.post('/api/setup/install-git').get_json(), {'ok': True})
        with mock.patch.object(routes, 'start_mac_install', return_value=False):
            response = self.client.post('/api/setup/install-git')
        self.assertEqual(response.status_code, 400)

    def test_a_real_sign_in_service_is_the_default(self) -> None:
        app = Flask('default')
        routes.register_quick_start_routes(app)
        self.assertEqual(app.test_client().get('/api/setup/claude-sign-in').get_json()['state'], 'idle')


if __name__ == '__main__':
    unittest.main()
