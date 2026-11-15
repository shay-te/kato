"""What kato could not reach shows on the screen, not only in the log.

A scan whose ticket-system fetch failed, and a git host that refused a pull
request lookup or a comment listing, used to leave the screen silent ("if there
is a fail to fetch from Jira the UI says nothing"). Each reports to ONE
service, ``ConnectionHealth``; the config status payload carries what is failing
until it answers again.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from kato_core_lib.data_layers.service import connection_health
from kato_core_lib.data_layers.service.agent_state_registry import AgentStateRegistry
from kato_core_lib.data_layers.service.connection_health import (
    TRACKER,
    ConnectionHealth,
    git_host_key,
    provider_label,
    report,
)
from kato_core_lib.data_layers.service.pull_request_lookup import LookupPaused
from kato_core_lib.data_layers.service.review_comment_service import ReviewCommentService
from kato_core_lib.jobs import process_assigned_tasks


class Clock(object):
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class ConnectionHealthTests(unittest.TestCase):
    def test_a_failure_is_kept_until_it_answers_again(self) -> None:
        clock = Clock()
        health = ConnectionHealth(tracker_label='jira', clock=clock)
        self.assertEqual(health.tracker_label, 'Jira')
        health.failed(TRACKER, label='Jira', kind='tracker', error=RuntimeError('401 Unauthorized'))
        clock.now = 150.0
        health.failed(TRACKER, label='Jira', kind='tracker', error='403 Forbidden')
        health.failed(git_host_key('Bitbucket'), label='Bitbucket', kind='git_host', error='timed out')
        self.assertEqual(health.problems(), [
            {'key': 'tracker', 'label': 'Jira', 'kind': 'tracker', 'since': 100.0, 'last': 150.0,
             'failures': 2, 'error': '403 Forbidden'},
            {'key': 'git:bitbucket', 'label': 'Bitbucket', 'kind': 'git_host', 'since': 150.0, 'last': 150.0,
             'failures': 1, 'error': 'timed out'},
        ])
        health.succeeded(TRACKER)
        health.succeeded('never-failed')
        self.assertEqual([problem['key'] for problem in health.problems()], ['git:bitbucket'])

    def test_an_error_with_no_words_is_named_and_a_long_one_is_cut(self) -> None:
        health = ConnectionHealth()
        self.assertEqual(health.tracker_label, 'the ticket system')
        health.failed('a', label='A', kind='tracker', error=TimeoutError())
        health.failed('b', label='B', kind='tracker', error='x' * 5000)
        errors = {problem['key']: problem['error'] for problem in health.problems()}
        self.assertEqual(errors['a'], 'TimeoutError')
        self.assertEqual(len(errors['b']), connection_health.MAX_ERROR_CHARS)

    def test_labels_and_keys(self) -> None:
        self.assertEqual(provider_label('youtrack'), 'YouTrack')
        self.assertEqual(provider_label('azure_devops'), 'Azure DevOps')
        self.assertEqual(provider_label('gitea'), 'gitea')
        self.assertEqual(provider_label(''), 'a service')
        self.assertEqual(git_host_key(' GitHub '), 'git:github')
        self.assertEqual(git_host_key(''), 'git:unknown')

    def test_report_tells_the_service_and_never_breaks_a_scan(self) -> None:
        report(None, TRACKER, error=RuntimeError('ignored'))  # nothing listening
        health = ConnectionHealth()
        report(health, TRACKER, error=RuntimeError('down'), label='Jira', kind='tracker')
        self.assertEqual(health.problems()[0]['error'], 'down')
        report(health, TRACKER)
        self.assertEqual(health.problems(), [])
        broken = Mock(failed=Mock(side_effect=RuntimeError('broken')))
        report(broken, TRACKER, error=RuntimeError('down'))  # swallowed


class TheScanReportsTheTicketSystemTests(unittest.TestCase):
    def service(self, **fetch):
        return SimpleNamespace(connection_health=ConnectionHealth(tracker_label='jira'),
                               get_assigned_tasks=Mock(**fetch))

    def test_a_failed_fetch_is_reported_and_still_fails_the_scan(self) -> None:
        service = self.service(side_effect=RuntimeError('HTTP 401: the token was refused'))
        with self.assertRaises(RuntimeError):
            process_assigned_tasks._fetch_assigned_tasks(service)
        problem = service.connection_health.problems()[0]
        self.assertEqual((problem['key'], problem['label'], problem['kind'], problem['error']),
                         ('tracker', 'Jira', 'tracker', 'HTTP 401: the token was refused'))

    def test_a_fetch_that_works_clears_it(self) -> None:
        service = self.service(return_value=['task'])
        service.connection_health.failed(TRACKER, label='Jira', kind='tracker', error='old')
        self.assertEqual(process_assigned_tasks._fetch_assigned_tasks(service), ['task'])
        self.assertEqual(service.connection_health.problems(), [])

    def test_a_service_with_no_health_still_scans(self) -> None:
        stub = SimpleNamespace(connection_health=Mock(), get_assigned_tasks=Mock(side_effect=RuntimeError('x')))
        with self.assertRaises(RuntimeError):
            process_assigned_tasks._fetch_assigned_tasks(stub)
        self.assertEqual(process_assigned_tasks._fetch_assigned_tasks(
            SimpleNamespace(get_assigned_tasks=Mock(return_value=[]))), [])


class TheReviewScanReportsEachGitHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.health = ConnectionHealth()
        self.repository = SimpleNamespace(id='api', provider='bitbucket')
        self.repository_service = SimpleNamespace(
            find_pull_requests=Mock(return_value=[{'id': '7'}]),
            get_repository=Mock(return_value=self.repository),
            list_pull_request_comments=Mock(return_value=['comment']),
        )
        self.service = ReviewCommentService(
            Mock(), Mock(), self.repository_service, AgentStateRegistry(), connection_health=self.health,
        )
        self.task = SimpleNamespace(id='T-1')

    def lookup(self):
        return self.service._looked_up_pull_requests(self.task, self.repository, 'T-1', 'T-1')

    def test_a_refused_lookup_names_the_host_and_an_answer_clears_it(self) -> None:
        self.repository_service.find_pull_requests.side_effect = RuntimeError('401: app password refused')
        self.assertEqual(self.lookup(), [])
        problem = self.health.problems()[0]
        self.assertEqual((problem['key'], problem['label'], problem['kind'], problem['error']),
                         ('git:bitbucket', 'Bitbucket', 'git_host', '401: app password refused'))
        self.repository_service.find_pull_requests.side_effect = None
        self.assertEqual(self.lookup(), [{'id': '7'}])
        self.assertEqual(self.health.problems(), [])

    def test_a_paused_host_is_neither_a_failure_nor_an_answer(self) -> None:
        self.repository_service.find_pull_requests.side_effect = LookupPaused('bitbucket.org', 60)
        self.assertEqual(self.lookup(), [])
        self.assertEqual(self.health.problems(), [])

    def test_comment_listing_reports_too_and_a_host_it_cannot_name_is_the_git_host(self) -> None:
        self.repository_service.list_pull_request_comments.side_effect = RuntimeError('timed out')
        self.repository.provider = ''
        self.assertEqual(self.service._pull_request_comments('api', '7'), [])
        problem = self.health.problems()[0]
        self.assertEqual((problem['key'], problem['label']), ('git:unknown', 'the git host'))
        self.repository_service.list_pull_request_comments.side_effect = None
        self.assertEqual(self.service._pull_request_comments('api', '7'), ['comment'])
        self.assertEqual(self.health.problems(), [])
        self.repository_service.get_repository.side_effect = KeyError('api')  # not a host's answer
        self.assertEqual(self.service._pull_request_comments('api', '7'), [])
        self.assertEqual(self.health.problems(), [])


class StatusPayloadTests(unittest.TestCase):
    def test_config_status_carries_what_is_failing(self) -> None:
        from kato_webserver.app import _connection_problems, create_app

        health = ConnectionHealth()
        health.failed(TRACKER, label='Jira', kind='tracker', error='401')
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {'KATO_SETTINGS_FILE': os.path.join(tmp, 'settings.json')}):
            app = create_app(session_manager=Mock(), agent_service=SimpleNamespace(connection_health=health),
                             needs_config=True)
            body = app.test_client().get('/api/config-status').get_json()
        self.assertEqual([problem['error'] for problem in body['connection_problems']], ['401'])
        self.assertEqual(_connection_problems(SimpleNamespace(config={})), [])
        broken = SimpleNamespace(problems=Mock(side_effect=RuntimeError('broken')))
        app.config['AGENT_SERVICE'] = SimpleNamespace(connection_health=broken)
        self.assertEqual(_connection_problems(app), [])


if __name__ == '__main__':
    unittest.main()
