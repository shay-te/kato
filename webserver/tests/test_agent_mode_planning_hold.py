"""``kato:wait-planning`` holds the task in Plan — as the webserver sees it.

Reported: "when I have put the wait-planning tag in the task on youtrack kato
will spawn the claude in Edit automatically this let's claude auto edit stuff
even while I am discussing things with him".

The composer and the chat route read only the operator's own pick, so a task
the tag was meant to keep in discussion showed "Edit automatically" and was
respawned that way. Every mode read now goes through one rule — Plan while
held, the operator's pick otherwise.

The hold is only the STARTING mode. Reported: "dont block me from changing
modes on the fly" — a different pick made while held is accepted and the hold
yields to it, with the tag still on the ticket.
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
    def _app(*, manager=None, workspace_manager=None):
        app = create_app(
            session_manager=manager or _Manager(),
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
    def test_another_pick_while_held_is_accepted_and_the_hold_yields(self) -> None:
        # THE REPORT: "Agent mode not changed — kato:wait-planning is on this
        # ticket". A pick made on the fly must win.
        app, client = self._app()
        set_planning_hold('T1', True)
        response = self._pick(client, 'bypassPermissions')
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertFalse(task_is_planning_held('T1'))
        self.assertEqual(
            self._read(client), {'mode': 'bypassPermissions', 'held_by_tag': ''},
        )
        self.assertFalse(app_module._task_is_plan_locked(app, 'T1'))

    def test_the_next_scan_seeing_the_tag_does_not_re_hold(self) -> None:
        _app, client = self._app()
        set_planning_hold('T1', True)
        self._pick(client, '')
        set_planning_hold('T1', True)  # the scan: tag still on the ticket
        self.assertEqual(self._read(client), {'mode': '', 'held_by_tag': ''})

    def test_picking_plan_while_held_keeps_the_hold(self) -> None:
        _app, client = self._app()
        set_planning_hold('T1', True)
        self.assertEqual(self._pick(client, 'plan').status_code, 200)
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
