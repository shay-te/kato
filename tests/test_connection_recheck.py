"""Connection status has ONE source; applying saved settings is ONE service.

"Test connection" said Connected while the banner kept saying kato could not
reach Jira: the banner read the startup error, the scans wrote their own
status, and the test checked the form — three paths, no two agreeing
(2026-11-15). Now kato's startup check, its scans and the Settings tab all go
through ``connection_health`` (what failed) and ``connection_recheck`` (put the
saved settings to work, or check again), and a gate keeps it that way.
"""
from __future__ import annotations

import ast
import json
import re
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

from kato_core_lib import main as main_module
from kato_core_lib.data_layers.service.connection_health import (
    TRACKER,
    ConnectionHealth,
    report_startup_step,
)
from kato_core_lib.data_layers.service.connection_recheck import (
    CHECKED,
    RESTART_NEEDED,
    RESTARTING,
    ConnectionRecheck,
    connection_settings_fingerprint,
)
from kato_core_lib.validation.startup_dependency_validator import StartupDependencyValidator
from kato_webserver.connection_routes import CONNECTION_RECHECK_KEY, register_connection_routes

REPO = Path(__file__).resolve().parents[1]


class Recorder(object):
    def __init__(self) -> None:
        self.restarts = 0
        self.checks = 0
        self.settings = {'JIRA_API_BASE_URL': 'https://site.atlassian.net', 'KATO_CHAT_AGENT': 'claude'}
        self.working: list[str] = []
        self.check_error: Exception | None = None

    def recheck(self) -> ConnectionRecheck:
        return ConnectionRecheck(
            check=self._check, saved_settings=lambda: dict(self.settings),
            working_tasks=lambda: list(self.working), restart=self._restart,
        )

    def _check(self) -> None:
        self.checks += 1
        if self.check_error:
            raise self.check_error

    def _restart(self) -> None:
        self.restarts += 1


class ConnectionRecheckTests(unittest.TestCase):
    def test_unchanged_settings_are_checked_again_and_the_answer_said(self) -> None:
        kato = Recorder()
        recheck = kato.recheck()
        kato.settings['KATO_CHAT_AGENT'] = 'codex'  # not a connection setting: no restart
        self.assertFalse(recheck.settings_changed())
        self.assertEqual(recheck.recheck(), {'status': CHECKED, 'ok': True, 'working': [], 'error': ''})
        kato.check_error = RuntimeError('401 from Jira')
        self.assertEqual(recheck.recheck()['error'], '401 from Jira')
        kato.check_error = RuntimeError('')
        self.assertEqual(recheck.recheck()['error'], 'RuntimeError')
        self.assertEqual((kato.checks, kato.restarts), (3, 0))

    def test_changed_connection_settings_restart_kato_to_use_them(self) -> None:
        kato = Recorder()
        recheck = kato.recheck()
        kato.settings['JIRA_API_BASE_URL'] = 'https://api.atlassian.com/ex/jira/cloud'
        self.assertTrue(recheck.settings_changed())
        self.assertEqual(recheck.recheck()['status'], RESTARTING)
        self.assertEqual((kato.restarts, kato.checks), (1, 0))

    def test_a_working_agent_is_never_cut_off_by_the_restart(self) -> None:
        kato = Recorder()
        recheck = kato.recheck()
        kato.settings['KATO_ISSUE_PLATFORM'] = 'jira'
        kato.working = ['UNA-1']
        answer = recheck.recheck()
        self.assertEqual((answer['status'], answer['working']), (RESTART_NEEDED, ['UNA-1']))
        self.assertIn('UNA-1', answer['error'])
        self.assertEqual(kato.restarts, 0)

    def test_an_unreadable_settings_file_is_never_a_change(self) -> None:
        kato = Recorder()
        recheck = kato.recheck()
        recheck._saved_settings = Mock(side_effect=OSError('locked'))
        self.assertFalse(recheck.settings_changed())

    def test_the_fingerprint_reads_only_connection_settings_and_skips_empty_ones(self) -> None:
        base = {'JIRA_API_TOKEN': 'a', 'REPOSITORY_ROOT_PATH': '/code'}
        self.assertEqual(connection_settings_fingerprint(base),
                         connection_settings_fingerprint({**base, 'KATO_CHAT_AGENT': 'x', 'JIRA_PROJECT': ''}))
        self.assertNotEqual(connection_settings_fingerprint(base),
                            connection_settings_fingerprint({**base, 'GITHUB_API_TOKEN': 'b'}))


class StartupCheckReportsIntoTheOneStatusTests(unittest.TestCase):
    def validator(self, tracker_error=None):
        repos = Mock()
        repos._repository_service = SimpleNamespace(_repositories=[SimpleNamespace(id='api')])
        task = SimpleNamespace(provider_name='jira', validate_connection=Mock(side_effect=tracker_error), max_retries=1)
        agent = SimpleNamespace(validate_connection=Mock(), max_retries=1)
        return StartupDependencyValidator(repos, task, agent, agent, skip_testing=True, agent_backend='claude')

    def test_each_step_reports_under_the_key_the_scans_use(self) -> None:
        health = ConnectionHealth()
        with self.assertRaises(RuntimeError):
            self.validator(RuntimeError('401 Unauthorized')).validate(
                Mock(), on_step=lambda kind, name, error: report_startup_step(health, kind, name, error))
        problems = {problem['key']: problem for problem in health.problems()}
        self.assertEqual(list(problems), [TRACKER])  # repositories and the agent got through
        self.assertEqual((problems[TRACKER]['label'], problems[TRACKER]['kind'], problems[TRACKER]['error']),
                         ('Jira', 'tracker', '401 Unauthorized'))
        # The next check that gets through clears the very same entry.
        self.validator().validate(Mock(), on_step=lambda kind, name, error: report_startup_step(health, kind, name, error))
        self.assertEqual(health.problems(), [])

    def test_repositories_and_the_agent_have_their_own_entries(self) -> None:
        health = ConnectionHealth(clock=lambda: 1.0)  # same moment: listed by key
        report_startup_step(health, 'repositories', 'repositories (api)', RuntimeError('no such folder'))
        report_startup_step(health, 'agent', 'claude', RuntimeError('not on PATH'))
        self.assertEqual([(p['key'], p['label'], p['kind']) for p in health.problems()], [
            ('agent:claude', 'Claude Code', 'settings'),
            ('repositories', 'your repositories', 'repositories'),
        ])

    def test_the_agent_service_check_feeds_its_connection_health(self) -> None:
        from kato_core_lib.data_layers.service.agent_service import AgentService

        service = AgentService.__new__(AgentService)
        service.logger = Mock()
        service.connection_health = ConnectionHealth()
        service._startup_validator = self.validator(RuntimeError('refused'))
        with self.assertRaises(RuntimeError):
            service.validate_connections()
        self.assertEqual([p['key'] for p in service.connection_health.problems()], [TRACKER])


class StartupLoopAppliesSavedSettingsTests(unittest.TestCase):
    def test_a_saved_fix_restarts_kato_instead_of_retrying_the_old_settings(self) -> None:
        kato = Recorder()
        app = SimpleNamespace(connection_recheck=kato.recheck())
        ticks = []

        def sleep(seconds):
            ticks.append(seconds)
            if len(ticks) == 3:
                kato.settings['JIRA_API_BASE_URL'] = 'https://api.atlassian.com/ex/jira/cloud'
        self.assertTrue(main_module._wait_or_apply_saved_settings(app, sleep))
        self.assertEqual((len(ticks), kato.restarts), (3, 1))

    def test_nothing_saved_waits_out_the_interval_and_retries(self) -> None:
        ticks = []
        self.assertFalse(main_module._wait_or_apply_saved_settings(SimpleNamespace(), ticks.append))
        self.assertAlmostEqual(sum(ticks), main_module._CONFIGURED_BOOT_VALIDATION_RETRY_SECONDS)
        kato = Recorder()
        app = SimpleNamespace(connection_recheck=kato.recheck())
        kato.settings['JIRA_EMAIL'] = 'changed'
        kato.working = ['UNA-1']  # an agent works: no restart, keep retrying
        self.assertFalse(main_module._wait_or_apply_saved_settings(app, lambda _s: None))
        self.assertEqual(kato.restarts, 0)

    def test_the_service_is_built_once_and_handed_to_the_webserver(self) -> None:
        self.assertIsNone(main_module._ensure_connection_recheck(SimpleNamespace(service=None)))
        self.assertIsNone(main_module._ensure_connection_recheck(SimpleNamespace(service=SimpleNamespace())))
        flask_app = SimpleNamespace(config={})
        app = SimpleNamespace(service=SimpleNamespace(validate_connections=Mock()), planning_flask_app=None)
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'KATO_SETTINGS_FILE': f'{tmp}/s.json'}):
            first = main_module._ensure_connection_recheck(app)
            app.planning_flask_app = flask_app
            self.assertIs(main_module._ensure_connection_recheck(app), first)
        self.assertIs(flask_app.config['CONNECTION_RECHECK'], first)
        self.assertFalse(first.settings_changed())
        self.assertEqual(first.recheck()['status'], CHECKED)
        app.service.validate_connections.assert_called_once_with()

    def test_restart_soon_answers_first_then_restarts(self) -> None:
        flask_app = SimpleNamespace(config={})
        app = SimpleNamespace(planning_flask_app=flask_app, logger=Mock())
        done = threading.Event()
        with patch.object(main_module, '_restart_in_place', side_effect=lambda _app: done.set()) as restart:
            main_module._restart_soon(app, delay_seconds=0.01)
            self.assertTrue(done.wait(5))
        restart.assert_called_once_with(app)
        self.assertIn('restarting to use your new settings', flask_app.config['SETUP_ERROR'])
        failed = threading.Event()

        def refuse(_app):
            failed.set()
            raise OSError('cannot exec')
        with patch.object(main_module, '_restart_in_place', side_effect=refuse):
            main_module._restart_soon(app, delay_seconds=0.0)
            self.assertTrue(failed.wait(5))
        for _ in range(50):  # the logging happens on the restart thread
            if app.logger.exception.called:
                break
            threading.Event().wait(0.02)
        app.logger.exception.assert_called()


class RouteTests(unittest.TestCase):
    def test_the_route_is_the_service_and_waits_for_it_to_exist(self) -> None:
        app = Flask(__name__)
        register_connection_routes(app)
        client = app.test_client()
        self.assertEqual(client.post('/api/connections/recheck').status_code, 503)
        app.config[CONNECTION_RECHECK_KEY] = SimpleNamespace(recheck=lambda: {'status': CHECKED, 'ok': True})
        self.assertEqual(client.post('/api/connections/recheck').get_json(), {'status': CHECKED, 'ok': True})


class OneSourceGateTests(unittest.TestCase):
    """Fails the build when a second connection-status path appears."""

    def python_sources(self):
        for root in ('kato_core_lib', 'webserver/kato_webserver'):
            for path in sorted((REPO / root).rglob('*.py')):
                if '/tests/' not in str(path) and path.name != '__init__.py':
                    yield path

    def test_only_connection_health_records_a_status(self) -> None:
        offenders = []
        for path in self.python_sources():
            if path.name == 'connection_health.py':
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ('failed', 'succeeded')
                        and 'health' in ast.unparse(node.func.value).lower()):
                    offenders.append(f'{path.relative_to(REPO)}:{node.lineno}')
        self.assertEqual(offenders, [], 'report into connection_health with report(), never a status of your own')

    def test_only_main_builds_the_apply_and_recheck_service(self) -> None:
        builders = [
            f'{path.relative_to(REPO)}:{number}'
            for path in self.python_sources() if path.name != 'connection_recheck.py'
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1)
            if re.search(r'\bConnectionRecheck\(', line)
        ]
        self.assertEqual(len(builders), 1, builders)
        self.assertTrue(builders[0].startswith('kato_core_lib/main.py:'), builders)

    def test_the_banner_reads_only_the_one_status(self) -> None:
        banner = (REPO / 'webserver/ui/src/components/ConnectionProblemsBanner.jsx').read_text(encoding='utf-8')
        self.assertNotIn('setup_error', banner.replace('Never read ``setup_error`` here', ''))
        consumers = [
            str(path.relative_to(REPO))
            for path in sorted((REPO / 'webserver/ui/src').rglob('*.jsx'))
            if not path.name.endswith('.test.jsx') and 'connection_problems' in path.read_text(encoding='utf-8')
            and path.name != 'ConnectionProblemsBanner.jsx'
        ]
        self.assertEqual(consumers, ['webserver/ui/src/App.jsx'])

    def test_settings_apply_only_through_the_one_route(self) -> None:
        callers = [
            str(path.relative_to(REPO))
            for path in sorted((REPO / 'webserver/ui/src').rglob('*.js*'))
            if not re.search(r'\.test\.jsx?$', path.name)
            and 'recheckConnections(' in path.read_text(encoding='utf-8') and path.name != 'api.js'
        ]
        self.assertEqual(callers, ['webserver/ui/src/components/ProviderCredentialsPanel.jsx'])


if __name__ == '__main__':
    unittest.main()
