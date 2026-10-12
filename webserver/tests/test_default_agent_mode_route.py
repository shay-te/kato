"""The default mode for new tasks, as the composer and the chat spawn see it.

A task with no mode of its own shows — and spawns in — the default from
Settings → Claude agent; a ``kato:wait-planning`` task still shows Plan; and
"Edit automatically" can still be picked while another default is set.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.helpers.plan_mode_store import DEFAULT_MODE_ENV
from kato_core_lib.helpers.planning_hold_store import set_planning_hold
from kato_webserver.app import create_app


class _Manager:
    def get_session(self, task_id):
        return None

    def get_record(self, task_id):
        return None

    def list_records(self):
        return []


class DefaultAgentModeRouteTests(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Path(tmp.name) / 'plan_mode.json'
        env = patch.dict(os.environ, {
            'KATO_PLAN_MODE_PATH': str(self.store),
            'KATO_PLANNING_HOLD_PATH': str(Path(tmp.name) / 'planning_holds.json'),
            DEFAULT_MODE_ENV: 'plan',
        })
        env.start()
        self.addCleanup(env.stop)
        self.client = create_app(session_manager=_Manager()).test_client()

    def mode(self, task_id: str = 'T1') -> str:
        return self.client.get(f'/api/sessions/{task_id}/agent-mode').get_json()['mode']

    def pick(self, mode: str, task_id: str = 'T1'):
        return self.client.post(f'/api/sessions/{task_id}/agent-mode', json={'mode': mode})

    def test_a_task_with_no_mode_of_its_own_shows_the_default(self) -> None:
        self.assertEqual(self.mode(), 'plan')

    def test_the_setting_applies_without_a_restart(self) -> None:
        # A Settings save mirrors into the environment; the next read sees it.
        os.environ[DEFAULT_MODE_ENV] = 'explain'
        self.assertEqual(self.mode(), 'explain')

    def test_a_pick_wins_over_the_default(self) -> None:
        self.assertEqual(self.pick('default').status_code, 200)
        self.assertEqual(self.mode(), 'default')
        self.assertEqual(self.mode('T2'), 'plan')  # only that task

    def test_edit_automatically_can_still_be_picked(self) -> None:
        response = self.pick('')
        self.assertEqual(response.get_json()['mode'], 'acceptEdits')
        self.assertEqual(self.mode(), 'acceptEdits')
        self.assertEqual(json.loads(self.store.read_text()), {'T1': 'acceptEdits'})

    def test_a_planning_held_task_still_starts_in_plan(self) -> None:
        os.environ[DEFAULT_MODE_ENV] = 'bypassPermissions'
        set_planning_hold('T1', True)
        self.assertEqual(self.mode(), 'plan')
        self.assertEqual(self.mode('T2'), 'bypassPermissions')

    def test_without_a_default_edit_automatically_is_no_mode_at_all(self) -> None:
        os.environ.pop(DEFAULT_MODE_ENV)
        self.assertEqual(self.pick('').get_json()['mode'], '')
        self.assertEqual(self.mode(), '')


if __name__ == '__main__':
    unittest.main()
