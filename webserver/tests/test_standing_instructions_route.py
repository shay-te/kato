"""Standing instructions on ``/api/all-settings``.

The text is read from settings.json at every spawn, so saving it already
applied it: the drawer must not ask for a restart, and must say when it takes
effect (the next chat — a running one keeps the prompt it started with). The
field is offered on the Prompts tab as a multi-line box, capped in length.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.helpers.standing_instructions_utils import (
    STANDING_INSTRUCTIONS_KEY as KEY,
    STANDING_INSTRUCTIONS_MAX_CHARS,
)
from kato_webserver.app import create_app


class _FakeManager:
    def list_records(self):
        return []
    def get_record(self, task_id):  # noqa: ARG002
        return None
    def get_session(self, task_id):  # noqa: ARG002
        return None


class StandingInstructionsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings_path = Path(self._tmp.name) / 'settings.json'
        patcher = patch.dict(
            os.environ, {'KATO_SETTINGS_FILE': str(self.settings_path)},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # ``_persist_settings`` mirrors saves into os.environ; keep that out
        # of the real process env once the test ends.
        self.addCleanup(os.environ.pop, KEY, None)
        self.addCleanup(os.environ.pop, 'KATO_LOG_LEVEL', None)
        os.environ.pop(KEY, None)
        self.client = create_app(session_manager=_FakeManager()).test_client()

    def _post(self, updates: dict):
        return self.client.post('/api/all-settings', json={'updates': updates})

    def _saved(self) -> dict:
        if not self.settings_path.is_file():
            return {}
        return json.loads(self.settings_path.read_text(encoding='utf-8'))

    def test_saving_applies_without_a_restart(self) -> None:
        text = 'Answer in English.\nEvery endpoint gets a test.'

        resp = self._post({KEY: text})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._saved()[KEY], text)
        body = resp.get_json()
        self.assertFalse(body['restart_required'])
        self.assertIn('next chat', body['message'])

    def test_saved_with_a_restart_only_key_the_banner_stays(self) -> None:
        resp = self._post({KEY: 'x', 'KATO_LOG_LEVEL': 'debug'})

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body['restart_required'])
        self.assertIn('Restart kato for the other change', body['message'])

    def test_a_text_past_the_cap_is_refused_and_not_saved(self) -> None:
        resp = self._post({KEY: 'x' * (STANDING_INSTRUCTIONS_MAX_CHARS + 1)})

        self.assertEqual(resp.status_code, 400)
        self.assertIn(KEY, resp.get_json()['error'])
        self.assertNotIn(KEY, self._saved())

    def test_offered_on_the_prompts_tab_as_a_multi_line_box(self) -> None:
        sections = self.client.get('/api/all-settings').get_json()['sections']
        general = next(s for s in sections if s['id'] == 'general')
        field = next(f for f in general['fields'] if f['key'] == KEY)

        self.assertEqual(field['type'], 'textarea')
        self.assertEqual(field['panel'], 'prompts')
        self.assertEqual(field['max_length'], STANDING_INSTRUCTIONS_MAX_CHARS)


if __name__ == '__main__':
    unittest.main()
