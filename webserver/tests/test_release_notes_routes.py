"""``/api/release-notes``: the notes as Settings point at them, and "Sync now"."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from flask import Flask

from kato_webserver.release_notes_routes import register_release_notes_routes


class ReleaseNotesRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.settings = self.root / 'settings.json'
        self.notes = self.root / 'release-notes.md'
        env = patch.dict(os.environ, {'KATO_SETTINGS_FILE': str(self.settings)})
        env.start()
        self.addCleanup(env.stop)
        self.settings.write_text(json.dumps({'KATO_RELEASE_NOTES_PATH': str(self.notes)}), encoding='utf-8')
        self.sync = MagicMock()
        self.sync.status.return_value = {'wanted': False, 'document_id': '', 'last_synced_at': 0.0, 'error': ''}
        app = Flask(__name__)
        register_release_notes_routes(app, google_sync=self.sync)
        self.client = app.test_client()

    def test_the_notes_are_read_from_the_file_settings_point_at(self) -> None:
        self.notes.write_text('# Release notes\n', encoding='utf-8')
        body = self.client.get('/api/release-notes').get_json()
        self.assertEqual(body['path'], str(self.notes))
        self.assertTrue(body['exists'])
        self.assertTrue(body['enabled'])
        self.assertEqual(body['markdown'], '# Release notes\n')
        self.assertFalse(body['google']['wanted'])

    def test_no_file_yet_is_an_empty_answer_not_an_error(self) -> None:
        body = self.client.get('/api/release-notes').get_json()
        self.assertFalse(body['exists'])
        self.assertEqual(body['markdown'], '')

    def test_sync_now_copies_when_the_google_doc_is_chosen(self) -> None:
        self.sync.wanted.return_value = True
        self.sync.sync_now.return_value = {'wanted': True, 'error': '', 'last_synced_at': 1.0, 'document_id': 'D'}
        resp = self.client.post('/api/release-notes/sync')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['document_id'], 'D')

    def test_sync_now_is_refused_when_the_copy_is_off(self) -> None:
        self.sync.wanted.return_value = False
        resp = self.client.post('/api/release-notes/sync')
        self.assertEqual(resp.status_code, 409)
        self.assertIn('Settings', resp.get_json()['error'])
        self.sync.sync_now.assert_not_called()

    def test_the_real_app_serves_it(self) -> None:
        from kato_webserver.app import create_app
        resp = create_app().test_client().get('/api/release-notes')
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
