"""Coverage for the schema-driven ``/api/all-settings`` route.

GET returns the full section schema + each field's resolved value
and source. POST writes only schema-declared keys to
``~/.kato/settings.json`` (the schema IS the whitelist) and never
touches the operator's ``.env``.

Both file locations are redirected to tmpfiles per-test so nothing
hits the real ``~/.kato`` or ``<repo>/.env``.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_webserver.app import create_app
from kato_core_lib.helpers.kato_settings_schema_utils import all_settings_keys


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
        self.tmp = Path(self._tmp.name)
        self.env_path = self.tmp / '.env'
        self.settings_path = self.tmp / 'settings.json'

    def _env(self, extra=None):
        base = {
            'KATO_SETTINGS_FILE': str(self.settings_path),
            'KATO_SETTINGS_ENV_FILE': str(self.env_path),
        }
        if extra:
            base.update(extra)
        return base

    def _client(self):
        return create_app(session_manager=_FakeManager()).test_client()

    def _saved(self):
        if not self.settings_path.is_file():
            return {}
        return json.loads(self.settings_path.read_text(encoding='utf-8'))


class AllSettingsGetTests(_Base):

    def test_returns_sections_with_fields(self) -> None:
        with patch.dict(os.environ, self._env()):
            resp = self._client().get('/api/all-settings')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        ids = {s['id'] for s in body['sections']}
        # The headline sections must be present.
        for expected in (
            'general', 'claude_agent', 'sandbox',
            'security_scanner', 'email_slack', 'openhands', 'aws',
        ):
            self.assertIn(expected, ids)
        # Every field carries the resolver fields the UI needs.
        sample = body['sections'][0]['fields'][0]
        for k in ('key', 'type', 'label', 'value', 'source'):
            self.assertIn(k, sample)

    def test_sandbox_section_carries_danger_metadata(self) -> None:
        with patch.dict(os.environ, self._env()):
            resp = self._client().get('/api/all-settings')
        sandbox = next(
            s for s in resp.get_json()['sections'] if s['id'] == 'sandbox'
        )
        bypass = next(
            f for f in sandbox['fields']
            if f['key'] == 'KATO_CLAUDE_BYPASS_PERMISSIONS'
        )
        self.assertTrue(bypass.get('danger'))

    def test_value_resolves_from_settings_json(self) -> None:
        self.settings_path.write_text(
            json.dumps({'KATO_MAX_PARALLEL_TASKS': '4'}), encoding='utf-8',
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('KATO_MAX_PARALLEL_TASKS', None)
            with patch.dict(os.environ, self._env()):
                resp = self._client().get('/api/all-settings')
        general = next(
            s for s in resp.get_json()['sections'] if s['id'] == 'general'
        )
        field = next(
            f for f in general['fields']
            if f['key'] == 'KATO_MAX_PARALLEL_TASKS'
        )
        self.assertEqual(field['value'], '4')
        self.assertEqual(field['source'], 'kato_settings')


class AllSettingsPostTests(_Base):

    def test_writes_whitelisted_keys_to_settings_json(self) -> None:
        self.env_path.write_text('# keep\nX=1\n', encoding='utf-8')
        env_before = self.env_path.read_text(encoding='utf-8')
        with patch.dict(os.environ, self._env()):
            resp = self._client().post(
                '/api/all-settings',
                json={'updates': {
                    'KATO_LOG_LEVEL': 'debug',
                    'KATO_SECURITY_SCANNER_ENABLED': True,
                }},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['restart_required'])
        saved = self._saved()
        self.assertEqual(saved['KATO_LOG_LEVEL'], 'debug')
        # Bool coerced to the ".env land" string form.
        self.assertEqual(saved['KATO_SECURITY_SCANNER_ENABLED'], 'true')
        # .env untouched.
        self.assertEqual(self.env_path.read_text(encoding='utf-8'), env_before)

    def test_unknown_key_is_dropped(self) -> None:
        with patch.dict(os.environ, self._env()):
            resp = self._client().post(
                '/api/all-settings',
                json={'updates': {
                    'KATO_LOG_LEVEL': 'info',
                    'TOTALLY_MADE_UP_KEY': 'x',
                    # A provider key is owned by another tab — the
                    # schema whitelist must NOT accept it here.
                    'YOUTRACK_API_TOKEN': 'leak',
                }},
            )
        self.assertEqual(resp.status_code, 200)
        saved = self._saved()
        self.assertIn('KATO_LOG_LEVEL', saved)
        self.assertNotIn('TOTALLY_MADE_UP_KEY', saved)
        self.assertNotIn('YOUTRACK_API_TOKEN', saved)

    def test_empty_updates_rejected(self) -> None:
        with patch.dict(os.environ, self._env()):
            resp = self._client().post('/api/all-settings', json={'updates': {}})
        self.assertEqual(resp.status_code, 400)

    def test_non_object_updates_rejected(self) -> None:
        with patch.dict(os.environ, self._env()):
            resp = self._client().post(
                '/api/all-settings', json={'updates': 'nope'},
            )
        self.assertEqual(resp.status_code, 400)

    def test_schema_whitelist_excludes_provider_and_repo_keys(self) -> None:
        # Guard the architectural boundary: provider + repo-root keys
        # have dedicated tabs and must NOT be in the generic whitelist.
        keys = all_settings_keys()
        for owned_elsewhere in (
            'REPOSITORY_ROOT_PATH', 'KATO_ISSUE_PLATFORM',
            'YOUTRACK_API_TOKEN', 'BITBUCKET_API_TOKEN',
        ):
            self.assertNotIn(owned_elsewhere, keys)


if __name__ == '__main__':
    unittest.main()
