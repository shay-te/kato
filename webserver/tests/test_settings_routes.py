"""Coverage for the operator-editable settings endpoints.

Storage model under test:

* WRITE target is ``~/.kato/settings.json`` (overridden per-test via
  ``KATO_SETTINGS_FILE``). The operator's ``<repo>/.env`` is NEVER
  modified — it stays a read-only legacy fallback.
* GET resolves a key across three stores, precedence
  ``live os.environ`` > ``settings.json`` > ``<repo>/.env`` >
  ``unset``, and labels the winning ``source`` so the UI can show
  where the value lives.

Both file locations are redirected to tmpfiles per-test
(``KATO_SETTINGS_FILE`` for settings.json, ``KATO_SETTINGS_ENV_FILE``
for the .env fallback) so nothing touches the real files.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_webserver.app import create_app


class _FakeManager:
    def list_records(self):
        return []
    def get_record(self, task_id):  # noqa: ARG002
        return None
    def get_session(self, task_id):  # noqa: ARG002
        return None


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)
        self.env_path = self.tmp_dir / '.env'           # legacy fallback
        self.settings_path = self.tmp_dir / 'settings.json'  # write target

    def _env(self, extra=None):
        base = {
            'KATO_SETTINGS_FILE': str(self.settings_path),
            'KATO_SETTINGS_ENV_FILE': str(self.env_path),
        }
        if extra:
            base.update(extra)
        return base


class SettingsGetTests(_Base):

    def test_unset_when_nothing_configured(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('REPOSITORY_ROOT_PATH', None)
            with patch.dict(os.environ, self._env()):
                app = create_app(session_manager=_FakeManager())
                response = app.test_client().get('/api/settings')
        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body['repository_root_path']['value'], '')
        self.assertEqual(body['repository_root_path']['source'], 'unset')

    def test_source_env_when_live_in_process(self) -> None:
        with patch.dict(os.environ, self._env({
            'REPOSITORY_ROOT_PATH': '/runtime/path',
        })):
            app = create_app(session_manager=_FakeManager())
            response = app.test_client().get('/api/settings')
        body = response.get_json()
        self.assertEqual(body['repository_root_path']['value'], '/runtime/path')
        self.assertEqual(body['repository_root_path']['source'], 'env')

    def test_source_kato_settings_when_only_settings_json_has_it(self) -> None:
        self.settings_path.write_text(
            json.dumps({'REPOSITORY_ROOT_PATH': '/from/settings'}),
            encoding='utf-8',
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('REPOSITORY_ROOT_PATH', None)
            with patch.dict(os.environ, self._env()):
                app = create_app(session_manager=_FakeManager())
                response = app.test_client().get('/api/settings')
        body = response.get_json()
        self.assertEqual(body['repository_root_path']['value'], '/from/settings')
        self.assertEqual(body['repository_root_path']['source'], 'kato_settings')

    def test_source_env_file_fallback(self) -> None:
        # Only the legacy .env has it → operator who hasn't migrated
        # still sees their value, labelled as the .env fallback.
        self.env_path.write_text(
            '# comment\nOTHER=keep\nREPOSITORY_ROOT_PATH=/from/env\n',
            encoding='utf-8',
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('REPOSITORY_ROOT_PATH', None)
            with patch.dict(os.environ, self._env()):
                app = create_app(session_manager=_FakeManager())
                response = app.test_client().get('/api/settings')
        body = response.get_json()
        self.assertEqual(body['repository_root_path']['value'], '/from/env')
        self.assertEqual(body['repository_root_path']['source'], 'env_file')

    def test_settings_json_wins_over_env_file(self) -> None:
        self.env_path.write_text('REPOSITORY_ROOT_PATH=/from/env\n', encoding='utf-8')
        self.settings_path.write_text(
            json.dumps({'REPOSITORY_ROOT_PATH': '/from/settings'}),
            encoding='utf-8',
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('REPOSITORY_ROOT_PATH', None)
            with patch.dict(os.environ, self._env()):
                app = create_app(session_manager=_FakeManager())
                response = app.test_client().get('/api/settings')
        body = response.get_json()
        self.assertEqual(body['repository_root_path']['value'], '/from/settings')
        self.assertEqual(body['repository_root_path']['source'], 'kato_settings')


class SettingsPostTests(_Base):

    def setUp(self) -> None:
        super().setUp()
        self.projects = self.tmp_dir / 'projects'
        self.projects.mkdir()

    def test_400_when_no_payload(self) -> None:
        with patch.dict(os.environ, self._env()):
            app = create_app(session_manager=_FakeManager())
            response = app.test_client().post('/api/settings', json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn('required', response.get_json()['error'])

    def test_400_when_path_does_not_exist(self) -> None:
        with patch.dict(os.environ, self._env()):
            app = create_app(session_manager=_FakeManager())
            response = app.test_client().post(
                '/api/settings',
                json={'repository_root_path': str(self.tmp_dir / 'ghost')},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn('does not exist', response.get_json()['error'])

    def test_400_when_path_is_a_file(self) -> None:
        file_path = self.tmp_dir / 'a-file.txt'
        file_path.write_text('hi', encoding='utf-8')
        with patch.dict(os.environ, self._env()):
            app = create_app(session_manager=_FakeManager())
            response = app.test_client().post(
                '/api/settings',
                json={'repository_root_path': str(file_path)},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn('not a directory', response.get_json()['error'])

    def test_writes_to_settings_json_not_env(self) -> None:
        # The operator's .env must be untouched; the value lands in
        # settings.json instead.
        self.env_path.write_text(
            '# keep me\nYOUTRACK_TOKEN=abc\n', encoding='utf-8',
        )
        env_before = self.env_path.read_text(encoding='utf-8')
        with patch.dict(os.environ, self._env()):
            app = create_app(session_manager=_FakeManager())
            response = app.test_client().post(
                '/api/settings',
                json={'repository_root_path': str(self.projects)},
            )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body['ok'])
        self.assertTrue(body['restart_required'])
        # .env is byte-for-byte unchanged.
        self.assertEqual(self.env_path.read_text(encoding='utf-8'), env_before)
        # settings.json now carries the value.
        self.assertTrue(self.settings_path.is_file())
        saved = json.loads(self.settings_path.read_text(encoding='utf-8'))
        self.assertEqual(
            saved['REPOSITORY_ROOT_PATH'], str(self.projects.resolve()),
        )

    def test_merges_into_existing_settings_json(self) -> None:
        self.settings_path.write_text(
            json.dumps({'KATO_ISSUE_PLATFORM': 'jira'}), encoding='utf-8',
        )
        with patch.dict(os.environ, self._env()):
            app = create_app(session_manager=_FakeManager())
            app.test_client().post(
                '/api/settings',
                json={'repository_root_path': str(self.projects)},
            )
        saved = json.loads(self.settings_path.read_text(encoding='utf-8'))
        # Pre-existing key preserved, new key merged in.
        self.assertEqual(saved['KATO_ISSUE_PLATFORM'], 'jira')
        self.assertIn('REPOSITORY_ROOT_PATH', saved)

    def test_expanduser_resolved(self) -> None:
        with patch.dict(os.environ, self._env({'HOME': str(self.tmp_dir)})):
            app = create_app(session_manager=_FakeManager())
            response = app.test_client().post(
                '/api/settings',
                json={'repository_root_path': '~/projects'},
            )
        self.assertEqual(response.status_code, 200)


if __name__ == '__main__':
    unittest.main()
