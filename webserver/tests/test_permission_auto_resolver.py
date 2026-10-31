"""A remembered permission is answered with no browser open.

Every test here makes no HTTP request at all: that is the point. Reaching the
answer through a route is what the UI's hidden-page polling and a closed lid
both take away.
"""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from kato_core_lib.helpers.tool_decision_store import remember_tool_decision
from kato_webserver.app import create_app
from kato_webserver.permission_auto_resolver import (
    TICK_SECONDS,
    build_and_start_permission_auto_resolver,
    resolve_remembered_permissions,
)


class _Session:
    def __init__(self, tool_name='Bash', tool_input=None, *, outside_sandbox=False) -> None:
        self.is_alive = True
        self.is_working = True
        self.cwd = '/work/T-1/repo'
        self.sandbox_allowed_paths = ()
        self.answers: list[dict] = []
        self.notices: list[tuple] = []
        self.answered = threading.Event()
        self._tool_name = tool_name
        self._tool_input = tool_input or {}
        self._outside_sandbox = outside_sandbox

    def allowed_additional_dirs(self):
        return ()

    def pending_control_request_tool(self) -> str:
        return '' if self.answers else self._tool_name

    def pending_request_input(self, request_id: str):
        return self._tool_name, dict(self._tool_input)

    def pending_control_requests(self):
        if self.answers:
            return []
        envelope = {
            'type': 'control_request',
            'request_id': 'r1',
            'request': {'tool_name': self._tool_name, 'input': dict(self._tool_input)},
        }
        if self._outside_sandbox:
            envelope['outside_sandbox'] = True
        return [envelope]

    def send_permission_response(self, **kwargs) -> None:
        self.answers.append(kwargs)
        self.answered.set()

    def publish_system_notice(self, subtype, message, extra=None) -> None:
        self.notices.append((subtype, message, extra))


class _Manager:
    def __init__(self, session) -> None:
        self._session = session
        self._record = SimpleNamespace(task_id='T-1', task_summary='')

    def list_records(self):
        return [self._record]

    def get_record(self, task_id):  # noqa: ARG002
        return self._record

    def get_session(self, task_id):  # noqa: ARG002
        return self._session


class ResolverTestCase(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = str(Path(folder.name) / 'tool_decisions.json')
        for patcher in (
            mock.patch.dict(os.environ, {'KATO_TOOL_DECISIONS_PATH': path}),
            mock.patch(
                'kato_core_lib.helpers.action_guard_config.read_kato_settings',
                return_value={},
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _app(self, session):
        return create_app(session_manager=_Manager(session))


class ResolveTests(ResolverTestCase):
    def test_a_remembered_allow_is_answered_with_no_browser(self):
        remember_tool_decision('Bash', 'mvn', True)
        session = _Session('Bash', {'command': 'mvn verify'})
        resolve_remembered_permissions(self._app(session))
        self.assertEqual(len(session.answers), 1)
        self.assertTrue(session.answers[0]['allow'])
        self.assertEqual(session.answers[0]['request_id'], 'r1')

    def test_a_remembered_deny_is_answered_too(self):
        remember_tool_decision('Bash', 'mvn', False)
        session = _Session('Bash', {'command': 'mvn verify'})
        resolve_remembered_permissions(self._app(session))
        self.assertFalse(session.answers[0]['allow'])

    def test_an_ask_with_no_standing_answer_still_waits_for_a_person(self):
        session = _Session('Bash', {'command': 'mvn verify'})
        resolve_remembered_permissions(self._app(session))
        self.assertEqual(session.answers, [])

    def test_a_question_for_the_operator_is_never_answered(self):
        remember_tool_decision('AskUserQuestion', '', True)
        session = _Session('AskUserQuestion', {'questions': [{
            'question': 'Which database?',
            'options': [{'label': 'Postgres'}, {'label': 'MySQL'}],
        }]})
        resolve_remembered_permissions(self._app(session))
        self.assertEqual(session.answers, [])

    def test_a_write_outside_the_task_folder_is_never_answered(self):
        remember_tool_decision('Edit', '', True)
        session = _Session('Edit', {'file_path': '/etc/hosts'}, outside_sandbox=True)
        resolve_remembered_permissions(self._app(session))
        self.assertEqual(session.answers, [])

    def test_leaving_plan_mode_is_never_answered(self):
        remember_tool_decision('ExitPlanMode', '', True)
        session = _Session('ExitPlanMode', {})
        resolve_remembered_permissions(self._app(session))
        self.assertEqual(session.answers, [])

    def test_nothing_pending_is_fine(self):
        session = _Session('Bash', {'command': 'mvn verify'})
        session.answers.append({})
        resolve_remembered_permissions(self._app(session))
        self.assertEqual(len(session.answers), 1)

    def test_no_session_manager_is_fine(self):
        resolve_remembered_permissions(create_app(session_manager=None))


class WorkerTests(ResolverTestCase):
    def test_answers_on_its_own_thread_with_no_request_made(self):
        remember_tool_decision('Bash', 'mvn', True)
        session = _Session('Bash', {'command': 'mvn verify'})
        resolver = build_and_start_permission_auto_resolver(
            self._app(session), tick_seconds=0.01,
        )
        self.addCleanup(resolver.stop)
        self.assertTrue(session.answered.wait(5))

    def test_a_tick_that_fails_does_not_end_the_worker(self):
        app = self._app(_Session())
        ticks = threading.Semaphore(0)

        def fails(*_args, **_kwargs):
            ticks.release()
            raise RuntimeError('boom')

        with mock.patch(
            'kato_webserver.permission_auto_resolver._pending_permission_tool_by_task', fails,
        ):
            resolver = build_and_start_permission_auto_resolver(app, tick_seconds=0.01)
            self.addCleanup(resolver.stop)
            self.assertTrue(ticks.acquire(timeout=5))
            self.assertTrue(ticks.acquire(timeout=5))

    def test_autostart_can_be_left_off(self):
        resolver = build_and_start_permission_auto_resolver(
            self._app(_Session()), autostart=False,
        )
        self.assertFalse(resolver.running)

    def test_an_answer_arrives_faster_than_a_person_would_notice(self):
        self.assertLessEqual(TICK_SECONDS, 2.0)


if __name__ == '__main__':
    unittest.main()
