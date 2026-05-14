"""Verify webserver routes fire lifecycle hooks at the right edges.

Wiring tests, not runner tests — we only care that the route calls
into the runner correctly. The route MUST tolerate ``hook_runner=None``
silently so embedded / test setups can boot without hooks installed.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from kato_core_lib.hooks.config import HookPoint
from kato_webserver.app import create_app


class _Record:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id


class _Session:
    def __init__(self) -> None:
        self.is_alive = True
        self.permission_calls: list[dict] = []

    def send_permission_response(self, **kwargs) -> None:
        self.permission_calls.append(kwargs)


class _Manager:
    def __init__(self, *, records=None, session=None, terminate_raises=False) -> None:
        self._records = records or []
        self._session = session
        self._terminate_raises = terminate_raises
        self.terminate_calls: list[str] = []

    def list_records(self):
        return list(self._records)

    def get_record(self, task_id: str):
        for record in self._records:
            if record.task_id == task_id:
                return record
        return None

    def get_session(self, task_id: str):  # noqa: ARG002
        return self._session

    def terminate_session(self, task_id: str) -> None:
        if self._terminate_raises:
            raise RuntimeError('stuck')
        self.terminate_calls.append(task_id)


class StopRouteHookTests(unittest.TestCase):

    def test_stop_route_fires_stop_hook_after_successful_terminate(self) -> None:
        hook_runner = MagicMock()
        app = create_app(
            session_manager=_Manager(records=[_Record('T-1')]),
            hook_runner=hook_runner,
        )

        response = app.test_client().post('/api/sessions/T-1/stop')

        self.assertEqual(response.status_code, 200)
        hook_runner.fire.assert_called_once()
        call = hook_runner.fire.call_args
        self.assertEqual(call.args[0], HookPoint.STOP)
        event = call.args[1]
        self.assertEqual(event['task_id'], 'T-1')
        self.assertEqual(event['source'], 'webserver_stop_route')

    def test_stop_route_does_NOT_fire_hook_when_terminate_fails(self) -> None:
        # The audit log must only see stops that went through. If
        # terminate_session raises, the operator's hook chain should
        # not be told a stop happened.
        hook_runner = MagicMock()
        app = create_app(
            session_manager=_Manager(
                records=[_Record('T-1')], terminate_raises=True,
            ),
            hook_runner=hook_runner,
        )

        response = app.test_client().post('/api/sessions/T-1/stop')

        self.assertEqual(response.status_code, 500)
        hook_runner.fire.assert_not_called()

    def test_stop_route_works_when_no_hook_runner_is_wired(self) -> None:
        # Default kato install (no hooks.json) → hook_runner=None.
        # The route must still behave correctly.
        app = create_app(session_manager=_Manager(records=[_Record('T-1')]))

        response = app.test_client().post('/api/sessions/T-1/stop')

        self.assertEqual(response.status_code, 200)


class PermissionRouteHookTests(unittest.TestCase):

    def _make_app(self, hook_runner=None):
        session = _Session()
        manager = _Manager(records=[_Record('T-1')], session=session)
        app = create_app(session_manager=manager, hook_runner=hook_runner)
        return app, session

    def test_no_runner_skips_pre_tool_use_and_still_delivers(self) -> None:
        app, session = self._make_app(hook_runner=None)

        response = app.test_client().post(
            '/api/sessions/T-1/permission',
            json={'request_id': 'r-1', 'allow': True, 'tool': 'Bash'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(session.permission_calls), 1)
        self.assertTrue(session.permission_calls[0]['allow'])

    def test_pre_tool_use_does_not_block_when_unconfigured(self) -> None:
        # Runner present but no hooks configured at pre_tool_use → fire()
        # returns [] → routing proceeds as normal.
        hook_runner = MagicMock()
        hook_runner.fire.return_value = []
        app, session = self._make_app(hook_runner=hook_runner)

        response = app.test_client().post(
            '/api/sessions/T-1/permission',
            json={'request_id': 'r-1', 'allow': True, 'tool': 'Bash'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(session.permission_calls[0]['allow'])
        # post_tool_use STILL fires — observers see every approved tool.
        fired = [c.args[0] for c in hook_runner.fire.call_args_list]
        self.assertIn(HookPoint.PRE_TOOL_USE, fired)
        self.assertIn(HookPoint.POST_TOOL_USE, fired)

    def test_pre_tool_use_blocks_flips_allow_to_deny(self) -> None:
        # When the operator's hook says "block", the route must
        # override the incoming allow=True and tell Claude allow=False.
        hook_runner = MagicMock()
        blocked_result = MagicMock(blocked=True, stderr='policy violation', error='')
        hook_runner.fire.return_value = [blocked_result]
        hook_runner.is_blocked.return_value = True
        app, session = self._make_app(hook_runner=hook_runner)

        response = app.test_client().post(
            '/api/sessions/T-1/permission',
            json={'request_id': 'r-1', 'allow': True, 'tool': 'Bash'},
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertFalse(body['allow'])
        # The session got allow=False with the hook's rationale.
        self.assertFalse(session.permission_calls[0]['allow'])
        self.assertIn('policy violation', session.permission_calls[0]['rationale'])

    def test_pre_tool_use_skipped_when_operator_already_denied(self) -> None:
        # No point firing pre_tool_use if the operator already
        # denied — there is nothing left to block.
        hook_runner = MagicMock()
        app, session = self._make_app(hook_runner=hook_runner)

        response = app.test_client().post(
            '/api/sessions/T-1/permission',
            json={'request_id': 'r-1', 'allow': False, 'tool': 'Bash'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(session.permission_calls[0]['allow'])
        # post_tool_use STILL fires — denials are part of the audit
        # log. pre_tool_use does NOT (nothing to block).
        fired = [c.args[0] for c in hook_runner.fire.call_args_list]
        self.assertNotIn(HookPoint.PRE_TOOL_USE, fired)
        self.assertIn(HookPoint.POST_TOOL_USE, fired)

    def test_post_tool_use_records_final_decision(self) -> None:
        hook_runner = MagicMock()
        hook_runner.fire.return_value = []
        app, _session = self._make_app(hook_runner=hook_runner)

        app.test_client().post(
            '/api/sessions/T-1/permission',
            json={'request_id': 'r-1', 'allow': True, 'tool': 'Edit'},
        )

        post_calls = [
            c for c in hook_runner.fire.call_args_list
            if c.args[0] == HookPoint.POST_TOOL_USE
        ]
        self.assertEqual(len(post_calls), 1)
        event = post_calls[0].args[1]
        self.assertEqual(event['task_id'], 'T-1')
        self.assertEqual(event['tool'], 'Edit')
        self.assertTrue(event['allow'])


if __name__ == '__main__':
    unittest.main()
