"""``kato:wait-planning`` holds the task in Plan — as the webserver sees it.

Reported: "when I have put the wait-planning tag in the task on youtrack kato
will spawn the claude in Edit automatically this let's claude auto edit stuff
even while I am discussing things with him".

The composer and the chat route read only the operator's own pick, so a task
the tag was meant to keep in discussion showed "Edit automatically" and was
respawned that way. Every mode read now goes through one rule — Plan while
held, the operator's pick otherwise — and a different pick made while held is
re-checked against the ticket, because the operator has usually just removed
the tag.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib.helpers.planning_hold_store import (
    set_planning_hold,
    task_is_planning_held,
)
from kato_webserver import app as app_module
from kato_webserver.app import create_app

TAG = 'kato:wait-planning'


class _Manager:
    def __init__(self, session=None) -> None:
        self.session = session
        self.terminated: list[tuple[str, bool]] = []

    def list_records(self):
        return []

    def get_record(self, task_id):  # noqa: ARG002
        return None

    def get_session(self, task_id):  # noqa: ARG002
        return self.session

    def terminate_session(self, task_id, remove_record=True):
        self.terminated.append((task_id, remove_record))
        self.session = None


class _AgentService:
    """Re-reads the ticket the way ``AgentService.refresh_planning_hold`` does."""

    def __init__(self, *, tag_still_on: bool) -> None:
        self.tag_still_on = tag_still_on
        self.rechecked: list[str] = []

    def refresh_planning_hold(self, task_id):
        self.rechecked.append(task_id)
        set_planning_hold(task_id, self.tag_still_on)
        return self.tag_still_on


class _HoldTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        patcher = patch.dict(os.environ, {
            'HOME': str(root / 'home'),
            'KATO_PLAN_MODE_PATH': str(root / 'plan_mode.json'),
            'KATO_PLANNING_HOLD_PATH': str(root / 'planning_holds.json'),
            'KATO_FORGOTTEN_TASKS_PATH': str(root / 'forgotten_tasks.json'),
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _app(*, manager=None, agent_service=None, workspace_manager=None):
        app = create_app(
            session_manager=manager or _Manager(),
            agent_service=agent_service,
            workspace_manager=workspace_manager,
        )
        return app, app.test_client()

    @staticmethod
    def _pick(client, mode):
        return client.post('/api/sessions/T1/agent-mode', json={'mode': mode})

    @staticmethod
    def _read(client):
        return client.get('/api/sessions/T1/agent-mode').get_json()


class HeldModeReadTests(_HoldTestCase):
    def test_a_held_task_reads_as_plan_whatever_the_operator_picked(self) -> None:
        _app, client = self._app()
        self._pick(client, 'bypassPermissions')
        set_planning_hold('T1', True)
        self.assertEqual(self._read(client), {'mode': 'plan', 'held_by_tag': TAG})

    def test_releasing_the_hold_gives_back_the_operators_pick(self) -> None:
        _app, client = self._app()
        self._pick(client, 'bypassPermissions')
        set_planning_hold('T1', True)
        set_planning_hold('T1', False)
        self.assertEqual(
            self._read(client), {'mode': 'bypassPermissions', 'held_by_tag': ''},
        )

    def test_an_unheld_task_names_no_tag(self) -> None:
        _app, client = self._app()
        self.assertEqual(self._read(client), {'mode': '', 'held_by_tag': ''})

    def test_a_hold_on_another_task_does_not_leak(self) -> None:
        _app, client = self._app()
        set_planning_hold('T2', True)
        self.assertEqual(self._read(client)['mode'], '')

    def test_mutating_tools_are_never_auto_approved_while_held(self) -> None:
        app, _client = self._app()
        set_planning_hold('T1', True)
        self.assertTrue(app_module._task_is_plan_locked(app, 'T1'))


class HeldModePickTests(_HoldTestCase):
    def test_another_pick_while_the_tag_is_on_is_refused_and_not_stored(self) -> None:
        agent = _AgentService(tag_still_on=True)
        app, client = self._app(agent_service=agent)
        set_planning_hold('T1', True)
        response = self._pick(client, '')
        self.assertEqual(response.status_code, 409)
        self.assertIn(TAG, response.get_json()['error'])
        self.assertEqual(agent.rechecked, ['T1'])
        self._pick(client, 'plan')
        set_planning_hold('T1', False)
        self.assertEqual(app.config['TASK_PLAN_MODE_OVERRIDES'].get('T1'), 'plan')

    def test_a_pick_right_after_the_tag_was_removed_is_accepted(self) -> None:
        agent = _AgentService(tag_still_on=False)
        _app, client = self._app(agent_service=agent)
        set_planning_hold('T1', True)
        response = self._pick(client, 'default')
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertFalse(task_is_planning_held('T1'))
        self.assertEqual(self._read(client), {'mode': 'default', 'held_by_tag': ''})

    def test_picking_plan_while_held_reads_no_ticket(self) -> None:
        agent = _AgentService(tag_still_on=True)
        _app, client = self._app(agent_service=agent)
        set_planning_hold('T1', True)
        self.assertEqual(self._pick(client, 'plan').status_code, 200)
        self.assertEqual(agent.rechecked, [])

    def test_an_ordinary_mode_change_reads_no_ticket(self) -> None:
        agent = _AgentService(tag_still_on=True)
        _app, client = self._app(agent_service=agent)
        self.assertEqual(self._pick(client, '').status_code, 200)
        self.assertEqual(agent.rechecked, [])

    def test_with_no_agent_service_the_recorded_hold_stands(self) -> None:
        _app, client = self._app()
        set_planning_hold('T1', True)
        self.assertEqual(self._pick(client, '').status_code, 409)

    def test_a_failed_ticket_read_keeps_the_hold(self) -> None:
        agent = SimpleNamespace(
            refresh_planning_hold=MagicMock(side_effect=RuntimeError('tracker down')),
        )
        _app, client = self._app(agent_service=agent)
        set_planning_hold('T1', True)
        self.assertEqual(self._pick(client, 'bypassPermissions').status_code, 409)
        self.assertTrue(task_is_planning_held('T1'))


class HeldTaskSpawnTests(_HoldTestCase):
    def test_an_idle_held_task_respawns_in_plan(self) -> None:
        # THE REPORT: the next message after the hold session went idle
        # respawned with the operator's pick ('' → acceptEdits).
        app, client = self._app()
        runner = MagicMock()
        app.config['PLANNING_SESSION_RUNNER'] = runner
        self._pick(client, '')
        set_planning_hold('T1', True)
        with app.test_request_context():
            app_module._spawn_or_reject_chat_session(app, 'T1', 'what do you think?')
        self.assertEqual(
            runner.resume_session_for_chat.call_args.kwargs['permission_mode'], 'plan',
        )

    def test_a_live_session_that_can_edit_is_replaced_on_the_next_message(self) -> None:
        manager = _Manager(SimpleNamespace(
            is_alive=True, is_working=True, permission_mode='acceptEdits',
            disallowed_tools='',
        ))
        app, _client = self._app(manager=manager)
        set_planning_hold('T1', True)
        self.assertTrue(
            app_module._plan_mode_change_needs_respawn(app, manager, 'T1', []),
        )

    def test_forgetting_the_task_releases_its_hold(self) -> None:
        workspace = SimpleNamespace(
            get=lambda task_id: None,
            delete=lambda task_id: None,
            workspace_path=lambda task_id: Path('/missing'),
        )
        _app, client = self._app(workspace_manager=workspace)
        set_planning_hold('T1', True)
        response = client.delete('/api/sessions/T1/workspace')
        self.assertNotEqual(response.status_code, 503)
        self.assertFalse(task_is_planning_held('T1'))


if __name__ == '__main__':
    unittest.main()
