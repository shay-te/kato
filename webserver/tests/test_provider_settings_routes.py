"""Coverage for the task-provider + git-provider settings routes.

Two distinct concepts share the same ``.env`` backing store:

* ``/api/task-providers`` — where tickets live + which platform
  kato polls. Has an "active" selector that writes
  ``KATO_ISSUE_PLATFORM``. Full per-platform field set.
* ``/api/git-providers`` — credentials kato uses to clone / push /
  open PRs. NO active selector (host inferred from repo URLs).
  Connection-level keys only; never touches KATO_ISSUE_PLATFORM.

The env file path is overridden per-test via
``KATO_SETTINGS_ENV_FILE`` so nothing touches the real
``<repo>/.env``.
"""

from __future__ import annotations

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


class _ProviderRouteTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env_path = Path(self._tmp.name) / '.env'           # legacy fallback
        self.settings_path = Path(self._tmp.name) / 'settings.json'  # write target

    def _client(self):
        app = create_app(session_manager=_FakeManager())
        return app.test_client()

    def _env(self, extra=None):
        base = {
            'KATO_SETTINGS_FILE': str(self.settings_path),
            'KATO_SETTINGS_ENV_FILE': str(self.env_path),
        }
        if extra:
            base.update(extra)
        return base

    def _saved(self):
        """Parsed settings.json after a POST (the new write target)."""
        import json
        if not self.settings_path.is_file():
            return {}
        return json.loads(self.settings_path.read_text(encoding='utf-8'))


class TaskProvidersGetTests(_ProviderRouteTestBase):

    def test_lists_all_task_platforms(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('KATO_ISSUE_PLATFORM', None)
            with patch.dict(os.environ, self._env()):
                resp = self._client().get('/api/task-providers')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        for name in ('youtrack', 'jira', 'github', 'gitlab', 'bitbucket'):
            self.assertIn(name, body['providers'])
        # Default active is youtrack when nothing is set.
        self.assertEqual(body['active'], 'youtrack')

    def test_active_reflects_env_var(self) -> None:
        with patch.dict(os.environ, self._env({'KATO_ISSUE_PLATFORM': 'jira'})):
            resp = self._client().get('/api/task-providers')
        self.assertEqual(resp.get_json()['active'], 'jira')


class TaskProvidersPostTests(_ProviderRouteTestBase):

    def test_switching_active_writes_platform_key(self) -> None:
        with patch.dict(os.environ, self._env()):
            resp = self._client().post(
                '/api/task-providers',
                json={'active': 'jira'},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['restart_required'])
        saved = self._saved()
        self.assertEqual(saved.get('KATO_ISSUE_PLATFORM'), 'jira')
        # The operator's .env is never touched.
        self.assertFalse(self.env_path.exists())

    def test_writes_only_whitelisted_fields(self) -> None:
        with patch.dict(os.environ, self._env()):
            resp = self._client().post(
                '/api/task-providers',
                json={
                    'provider': 'jira',
                    'fields': {
                        'JIRA_API_TOKEN': 'tok-123',
                        'KATO_CLAUDE_BYPASS_PERMISSIONS': 'true',  # must be ignored
                    },
                },
            )
        self.assertEqual(resp.status_code, 200)
        saved = self._saved()
        self.assertEqual(saved.get('JIRA_API_TOKEN'), 'tok-123')
        self.assertNotIn('KATO_CLAUDE_BYPASS_PERMISSIONS', saved)

    def test_unknown_provider_rejected(self) -> None:
        with patch.dict(os.environ, self._env()):
            resp = self._client().post(
                '/api/task-providers',
                json={'active': 'subversion'},
            )
        self.assertEqual(resp.status_code, 400)

    def test_empty_payload_rejected(self) -> None:
        with patch.dict(os.environ, self._env()):
            resp = self._client().post('/api/task-providers', json={})
        self.assertEqual(resp.status_code, 400)


class GitProvidersGetTests(_ProviderRouteTestBase):

    def test_only_git_hosts_no_active_selector(self) -> None:
        with patch.dict(os.environ, self._env()):
            resp = self._client().get('/api/git-providers')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(
            sorted(body['providers'].keys()),
            ['bitbucket', 'github', 'gitlab'],
        )
        # No task trackers here.
        self.assertNotIn('youtrack', body['providers'])
        self.assertNotIn('jira', body['providers'])
        # And crucially: no "active" field — git host is inferred.
        self.assertNotIn('active', body)


class GitProvidersPostTests(_ProviderRouteTestBase):

    def test_writes_host_creds_without_touching_platform(self) -> None:
        with patch.dict(os.environ, self._env()):
            resp = self._client().post(
                '/api/git-providers',
                json={
                    'provider': 'github',
                    'fields': {'GITHUB_API_TOKEN': 'ghp_abc'},
                },
            )
        self.assertEqual(resp.status_code, 200)
        saved = self._saved()
        self.assertEqual(saved.get('GITHUB_API_TOKEN'), 'ghp_abc')
        # The git-providers route must NEVER write the platform key.
        self.assertNotIn('KATO_ISSUE_PLATFORM', saved)

    def test_rejects_a_tracker_as_git_host(self) -> None:
        # YouTrack / Jira are not git hosts — must 400.
        with patch.dict(os.environ, self._env()):
            resp = self._client().post(
                '/api/git-providers',
                json={'provider': 'youtrack', 'fields': {'X': 'y'}},
            )
        self.assertEqual(resp.status_code, 400)

    def test_issue_only_fields_not_writable_via_git_route(self) -> None:
        # ``GITHUB_ISSUE_STATES`` is a task-provider field, not a
        # git-host connection key — the git route must drop it.
        with patch.dict(os.environ, self._env()):
            resp = self._client().post(
                '/api/git-providers',
                json={
                    'provider': 'github',
                    'fields': {
                        'GITHUB_API_TOKEN': 'ghp_ok',
                        'GITHUB_ISSUE_STATES': 'open',
                    },
                },
            )
        self.assertEqual(resp.status_code, 200)
        saved = self._saved()
        self.assertEqual(saved.get('GITHUB_API_TOKEN'), 'ghp_ok')
        self.assertNotIn('GITHUB_ISSUE_STATES', saved)


if __name__ == '__main__':
    unittest.main()
