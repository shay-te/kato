"""Action Guard (Layer B) enforcement in the permission route.

Verifies the webserver re-derives the tool SERVER-SIDE, blocks a dangerous
action even when the operator clicks Allow, leaves benign actions alone,
honours the master switch, and writes the tamper-evident audit line.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from kato_webserver.app import create_app


class _Record:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.task_summary = ''


class _AGSession:
    def __init__(self, tool_name='Bash', tool_input=None) -> None:
        self.is_alive = True
        self._tool_name = tool_name
        self._tool_input = tool_input or {}
        self.cwd = '/work/UNA-1/repo'
        self.sandbox_allowed_paths = ()
        self.permission_calls: list[dict] = []
        self.notices: list[tuple] = []

    def allowed_additional_dirs(self):
        return ()

    def pending_request_input(self, request_id: str):
        return self._tool_name, dict(self._tool_input)

    def send_permission_response(self, **kwargs) -> None:
        self.permission_calls.append(kwargs)

    def publish_system_notice(self, subtype, message, extra=None) -> None:
        self.notices.append((subtype, message, extra))


class _Manager:
    def __init__(self, *, records=None, session=None) -> None:
        self._records = records or []
        self._session = session

    def list_records(self):
        return list(self._records)

    def get_record(self, task_id: str):
        for record in self._records:
            if record.task_id == task_id:
                return record
        return None

    def get_session(self, task_id: str):  # noqa: ARG002
        return self._session


class ActionGuardRouteTests(unittest.TestCase):
    def _app(self, session):
        manager = _Manager(records=[_Record('T-1')], session=session)
        return create_app(session_manager=manager)

    def _post(self, app, body, env=None):
        # Isolate from the operator's real settings.json; control posture
        # purely through env (so tests never touch ~/.kato).
        with mock.patch(
            'kato_core_lib.helpers.action_guard_config.read_kato_settings',
            return_value={},
        ), mock.patch.dict(os.environ, env or {}, clear=False):
            return app.test_client().post(
                '/api/sessions/T-1/permission', json=body,
            )

    def test_blocks_catastrophic_command_even_when_operator_allows(self):
        session = _AGSession('Bash', {'command': 'rm -rf /'})
        app = self._app(session)
        resp = self._post(app, {'request_id': 'r1', 'allow': True})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()['allow'])
        self.assertFalse(session.permission_calls[-1]['allow'])
        # A BLOCK bubble was published to the feed.
        self.assertTrue(session.notices)
        self.assertEqual(session.notices[-1][0], 'kato_action_guard_block')

    def test_server_side_derivation_ignores_client_supplied_command(self):
        # The body lies (benign tool/command); the PENDING request is the
        # real, dangerous one — the guard must use the server-side input.
        session = _AGSession('Bash', {'command': 'cat ~/.ssh/id_rsa'})
        app = self._app(session)
        resp = self._post(app, {
            'request_id': 'r1', 'allow': True, 'tool': 'Read', 'command': 'ls',
        })
        self.assertFalse(resp.get_json()['allow'])

    def test_benign_command_is_delivered(self):
        session = _AGSession('Bash', {'command': 'git status'})
        app = self._app(session)
        resp = self._post(app, {'request_id': 'r1', 'allow': True})
        self.assertTrue(resp.get_json()['allow'])
        self.assertTrue(session.permission_calls[-1]['allow'])
        self.assertFalse(session.notices)

    def test_master_switch_off_does_not_block(self):
        session = _AGSession('Bash', {'command': 'rm -rf /'})
        app = self._app(session)
        resp = self._post(
            app, {'request_id': 'r1', 'allow': True},
            env={'KATO_ACTION_GUARD_ENABLED': 'false'},
        )
        # Layer B is off → the dangerous command is NOT flipped to deny here
        # (Layer A + Docker remain the structural floor).
        self.assertTrue(resp.get_json()['allow'])

    def test_operator_deny_is_respected(self):
        session = _AGSession('Bash', {'command': 'rm -rf build/'})
        app = self._app(session)
        resp = self._post(app, {'request_id': 'r1', 'allow': False})
        self.assertFalse(resp.get_json()['allow'])

    def test_audit_records_block_decision(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'audit.log')
            session = _AGSession('Bash', {'command': 'rm -rf /'})
            app = self._app(session)
            self._post(
                app, {'request_id': 'r1', 'allow': True},
                env={'KATO_ACTION_GUARD_AUDIT_PATH': path},
            )
            from kato_core_lib.helpers.action_guard_audit import (
                read_action_guard_audit,
            )
            rows = read_action_guard_audit(audit_log_path=path)
            self.assertTrue(rows)
            self.assertEqual(rows[-1]['decision'], 'block')
            self.assertEqual(rows[-1]['category'], 'destructive_fs')
            # The raw destructive command is never stored verbatim.
            self.assertTrue(rows[-1]['command_digest'].startswith('sha256:'))


if __name__ == '__main__':
    unittest.main()
