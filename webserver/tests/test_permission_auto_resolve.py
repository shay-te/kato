"""Backend-owned auto-resolve for remembered permission decisions.

The client used to independently decide whether to auto-submit a
remembered "Allow always"/"Deny always" choice without ever showing the
modal — the backend had zero memory of that choice. This moves the
decision server-side: a still-pending request that matches a remembered
decision is resolved BEFORE it is ever surfaced to the browser (neither
the ``/api/permissions/pending`` poll nor the per-task SSE stream), and
the same safety carve-outs the client used to apply (never for an
AskUserQuestion-shaped ask, an out-of-sandbox write, or a high-risk
Action Guard category) are preserved server-side.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kato_webserver.app import _replay_session_backlog, create_app


class _Record:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.task_summary = ''


class _Session:
    def __init__(self, tool_name='Bash', tool_input=None, outside_sandbox=False) -> None:
        self.is_alive = True
        self._tool_name = tool_name
        self._tool_input = tool_input or {}
        self._outside_sandbox = outside_sandbox
        self.cwd = '/work/UNA-1/repo'
        self.sandbox_allowed_paths = ()
        self.permission_calls: list[dict] = []
        self.notices: list[tuple] = []

    def allowed_additional_dirs(self):
        return ()

    def pending_request_input(self, request_id: str):
        return self._tool_name, dict(self._tool_input)

    def pending_control_requests(self):
        envelope = {
            'type': 'control_request',
            'request_id': 'r1',
            'request': {'tool_name': self._tool_name, 'input': dict(self._tool_input)},
        }
        if self._outside_sandbox:
            envelope['outside_sandbox'] = True
        return [envelope]

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


class _BaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        path = str(Path(self._td.name) / 'tool_decisions.json')
        patcher = mock.patch.dict(os.environ, {'KATO_TOOL_DECISIONS_PATH': path})
        patcher.start()
        self.addCleanup(patcher.stop)
        # Isolate Action Guard from the operator's real settings.json —
        # same pattern as test_action_guard_route.py.
        settings_patcher = mock.patch(
            'kato_core_lib.helpers.action_guard_config.read_kato_settings',
            return_value={},
        )
        settings_patcher.start()
        self.addCleanup(settings_patcher.stop)

    def _app(self, session):
        manager = _Manager(records=[_Record('T-1')], session=session)
        return create_app(session_manager=manager)


class PendingListAutoResolveTests(_BaseCase):
    def test_remembered_allow_auto_resolves_before_reaching_pending_list(self) -> None:
        from kato_core_lib.helpers.tool_decision_store import remember_tool_decision
        remember_tool_decision('Bash', 'mvn', True)
        session = _Session('Bash', {'command': 'mvn verify'})
        app = self._app(session)

        resp = app.test_client().get('/api/permissions/pending')

        self.assertEqual(resp.get_json()['pending'], [])
        self.assertEqual(len(session.permission_calls), 1)
        self.assertTrue(session.permission_calls[0]['allow'])
        self.assertEqual(session.permission_calls[0]['request_id'], 'r1')

    def test_remembered_deny_auto_resolves_to_deny(self) -> None:
        from kato_core_lib.helpers.tool_decision_store import remember_tool_decision
        remember_tool_decision('Bash', 'rm', False)
        session = _Session('Bash', {'command': 'rm -rf target'})
        app = self._app(session)

        resp = app.test_client().get('/api/permissions/pending')

        self.assertEqual(resp.get_json()['pending'], [])
        self.assertFalse(session.permission_calls[0]['allow'])

    def test_no_remembered_decision_still_surfaces_the_ask(self) -> None:
        session = _Session('Bash', {'command': 'docker ps'})
        app = self._app(session)

        resp = app.test_client().get('/api/permissions/pending')

        self.assertEqual(len(resp.get_json()['pending']), 1)
        self.assertEqual(session.permission_calls, [])

    def test_high_risk_category_is_never_auto_resolved(self) -> None:
        # Even a matching remembered "allow" for the same program must
        # not silently ride through a NEWLY high-risk invocation.
        from kato_core_lib.helpers.tool_decision_store import remember_tool_decision
        remember_tool_decision('Bash', 'cat', True)
        session = _Session('Bash', {'command': 'cat ~/.ssh/id_rsa'})
        app = self._app(session)

        resp = app.test_client().get('/api/permissions/pending')

        self.assertEqual(len(resp.get_json()['pending']), 1)
        self.assertEqual(session.permission_calls, [])

    def test_outside_sandbox_is_never_auto_resolved(self) -> None:
        from kato_core_lib.helpers.tool_decision_store import remember_tool_decision
        remember_tool_decision('Bash', 'mvn', True)
        session = _Session('Bash', {'command': 'mvn verify'}, outside_sandbox=True)
        app = self._app(session)

        resp = app.test_client().get('/api/permissions/pending')

        self.assertEqual(len(resp.get_json()['pending']), 1)
        self.assertEqual(session.permission_calls, [])

    def test_ask_user_question_is_never_auto_resolved(self) -> None:
        from kato_core_lib.helpers.tool_decision_store import remember_tool_decision
        # Simulates a stale/unexpected entry — the route must never
        # honour it for a question-shaped ask regardless.
        remember_tool_decision('AskUserQuestion', '', True)
        session = _Session('AskUserQuestion', {
            'questions': [{'question': 'Which library?', 'options': [{'label': 'A'}]}],
        })
        app = self._app(session)

        resp = app.test_client().get('/api/permissions/pending')

        self.assertEqual(len(resp.get_json()['pending']), 1)
        self.assertEqual(session.permission_calls, [])

    def test_probe_failure_is_logged_not_silently_swallowed(self) -> None:
        # Regression: pending_control_requests() raising used to be a
        # silent `except Exception: continue` — indistinguishable from
        # "this session genuinely has nothing pending." This is the
        # operator's ONLY visibility into a backgrounded task's pending
        # tool-approval ask, so a systemic failure here must at least
        # be logged (best-effort behavior — skip this session, don't
        # fail the whole feed — stays unchanged).
        session = _Session('Bash', {'command': 'docker ps'})
        session.pending_control_requests = mock.Mock(side_effect=RuntimeError('boom'))
        app = self._app(session)

        with mock.patch.object(app.logger, 'exception') as mock_exception:
            resp = app.test_client().get('/api/permissions/pending')

        self.assertEqual(resp.get_json()['pending'], [])
        mock_exception.assert_called_once()


class PermissionRouteRememberTests(_BaseCase):
    def test_remember_true_persists_allow_decision(self) -> None:
        from kato_core_lib.helpers.tool_decision_store import recall_tool_decision
        session = _Session('Bash', {'command': 'mvn verify'})
        app = self._app(session)

        resp = app.test_client().post(
            '/api/sessions/T-1/permission',
            json={'request_id': 'r1', 'allow': True, 'remember': True},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(recall_tool_decision('Bash', 'mvn'))

    def test_remember_true_persists_deny_decision(self) -> None:
        from kato_core_lib.helpers.tool_decision_store import recall_tool_decision
        session = _Session('Bash', {'command': 'rm -rf target'})
        app = self._app(session)

        app.test_client().post(
            '/api/sessions/T-1/permission',
            json={'request_id': 'r1', 'allow': False, 'remember': True},
        )

        self.assertFalse(recall_tool_decision('Bash', 'rm'))

    def test_remember_false_does_not_persist(self) -> None:
        from kato_core_lib.helpers.tool_decision_store import recall_tool_decision
        session = _Session('Bash', {'command': 'mvn verify'})
        app = self._app(session)

        app.test_client().post(
            '/api/sessions/T-1/permission',
            json={'request_id': 'r1', 'allow': True},
        )

        self.assertIsNone(recall_tool_decision('Bash', 'mvn'))

    def test_remember_true_on_a_question_is_never_persisted(self) -> None:
        from kato_core_lib.helpers.tool_decision_store import recall_tool_decision
        session = _Session('AskUserQuestion', {
            'questions': [{'question': 'Which?', 'options': [{'label': 'A'}]}],
        })
        app = self._app(session)

        app.test_client().post(
            '/api/sessions/T-1/permission',
            json={'request_id': 'r1', 'allow': True, 'remember': True},
        )

        self.assertIsNone(recall_tool_decision('AskUserQuestion', ''))


class _ControlRequestEvent:
    def __init__(self, request_id, tool_name, tool_input, outside_sandbox=False):
        self._request_id = request_id
        self._tool_name = tool_name
        self._tool_input = tool_input
        self._outside_sandbox = outside_sandbox

    def to_dict(self):
        raw = {
            'type': 'control_request',
            'request_id': self._request_id,
            'request': {'tool_name': self._tool_name, 'input': self._tool_input},
        }
        if self._outside_sandbox:
            raw['outside_sandbox'] = True
        return {'raw': raw, 'received_at_epoch': 1.0}


class _SessionWithBacklog(_Session):
    def __init__(self, events, **kwargs) -> None:
        super().__init__(**kwargs)
        self._events = events

    def recent_events(self):
        return self._events


class SseBacklogAutoResolveTests(_BaseCase):
    def test_auto_resolvable_control_request_is_not_replayed_over_sse(self) -> None:
        from kato_core_lib.helpers.tool_decision_store import remember_tool_decision
        remember_tool_decision('Bash', 'mvn', True)
        session = _SessionWithBacklog(
            [_ControlRequestEvent('r1', 'Bash', {'command': 'mvn verify'})],
            tool_name='Bash', tool_input={'command': 'mvn verify'},
        )
        app = self._app(session)

        frames = [f for _e, f in _replay_session_backlog(session, app=app)]

        self.assertEqual(frames, [])
        self.assertEqual(len(session.permission_calls), 1)
        self.assertTrue(session.permission_calls[0]['allow'])

    def test_non_matching_control_request_is_still_replayed_over_sse(self) -> None:
        session = _SessionWithBacklog(
            [_ControlRequestEvent('r1', 'Bash', {'command': 'docker ps'})],
            tool_name='Bash', tool_input={'command': 'docker ps'},
        )
        app = self._app(session)

        frames = [f for _e, f in _replay_session_backlog(session, app=app)]

        self.assertEqual(len(frames), 1)
        self.assertIn('"type": "control_request"', frames[0])
        self.assertEqual(session.permission_calls, [])

    def test_without_an_app_auto_resolve_is_skipped(self) -> None:
        # Direct-generator call sites (existing tests, no Flask app in
        # scope) must behave exactly as before this feature existed.
        from kato_core_lib.helpers.tool_decision_store import remember_tool_decision
        remember_tool_decision('Bash', 'mvn', True)
        session = _SessionWithBacklog(
            [_ControlRequestEvent('r1', 'Bash', {'command': 'mvn verify'})],
            tool_name='Bash', tool_input={'command': 'mvn verify'},
        )

        frames = [f for _e, f in _replay_session_backlog(session)]

        self.assertEqual(len(frames), 1)
        self.assertEqual(session.permission_calls, [])


class ToolDecisionsRouteTests(_BaseCase):
    def test_list_and_forget_roundtrip(self) -> None:
        from kato_core_lib.helpers.tool_decision_store import remember_tool_decision
        remember_tool_decision('Bash', 'mvn', True)
        app = self._app(_Session())
        client = app.test_client()

        listed = client.get('/api/tool-decisions').get_json()['decisions']
        self.assertEqual(
            listed,
            [{'tool_name': 'Bash', 'command_signature': 'mvn', 'allow': True}],
        )

        forget_resp = client.post(
            '/api/tool-decisions/forget',
            json={'tool_name': 'Bash', 'command_signature': 'mvn'},
        )
        self.assertEqual(forget_resp.status_code, 200)
        self.assertEqual(client.get('/api/tool-decisions').get_json()['decisions'], [])

    def test_forget_requires_tool_name(self) -> None:
        app = self._app(_Session())
        resp = app.test_client().post('/api/tool-decisions/forget', json={})
        self.assertEqual(resp.status_code, 400)

    def test_set_changes_scope_of_an_existing_entry(self) -> None:
        from kato_core_lib.helpers.tool_decision_store import remember_tool_decision, recall_tool_decision
        remember_tool_decision('Bash', 'mvn', True)
        app = self._app(_Session())

        resp = app.test_client().post(
            '/api/tool-decisions/set',
            json={'tool_name': 'Bash', 'command_signature': 'mvn', 'allow': False},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(recall_tool_decision('Bash', 'mvn'))

    def test_set_requires_tool_name(self) -> None:
        app = self._app(_Session())
        resp = app.test_client().post('/api/tool-decisions/set', json={'allow': True})
        self.assertEqual(resp.status_code, 400)

    def test_clear_removes_every_decision(self) -> None:
        from kato_core_lib.helpers.tool_decision_store import remember_tool_decision
        remember_tool_decision('Bash', 'mvn', True)
        remember_tool_decision('Edit', '', True)
        app = self._app(_Session())
        client = app.test_client()

        resp = client.post('/api/tool-decisions/clear')

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(client.get('/api/tool-decisions').get_json()['decisions'], [])


if __name__ == '__main__':
    unittest.main()
