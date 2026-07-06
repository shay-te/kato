"""Coverage for the ``/api/config-status`` endpoint — the source of truth
the setup-mode onboarding gate polls.

The endpoint answers two questions with real config evaluation (no mocks of
the validator): (1) did THIS process boot unconfigured (``setup_mode``), and
(2) is the config complete RIGHT NOW across the layered settings stores
(``needs_config`` / ``missing``). The second is evaluated over
``env > settings.json`` — the same precedence the Settings UI uses — so a
value saved to ``settings.json`` clears the missing item WITHOUT a restart.
(``.env`` support was removed entirely; settings.json is kato's only file.)

The settings path is redirected to a tmpfile per-test
(``KATO_SETTINGS_FILE``) so nothing touches the operator's real file.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_webserver.app import create_app


# Every key the youtrack + openhands ``mode='all'`` check requires. Popped
# from ``os.environ`` per-test so the developer's own shell config can't leak
# in and make an "unconfigured" scenario look configured (or vice-versa).
_REQUIRED_KEYS = (
    'YOUTRACK_API_BASE_URL',
    'YOUTRACK_API_TOKEN',
    'YOUTRACK_PROJECT',
    'YOUTRACK_ASSIGNEE',
    'REPOSITORY_ROOT_PATH',
    'OPENHANDS_BASE_URL',
    'OPENHANDS_API_KEY',
    'OH_SECRET_KEY',
    'OPENHANDS_LLM_MODEL',
    'OPENHANDS_LLM_API_KEY',
    # These could silently switch the platform / backend and change which
    # keys are required, so isolate them too.
    'KATO_ISSUE_PLATFORM',
    'KATO_AGENT_BACKEND',
)


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
        self.settings_path = self.tmp_dir / 'settings.json'  # the ONLY store
        self.projects = self.tmp_dir / 'projects'            # a real repo root
        self.projects.mkdir()

    def _env(self, extra=None):
        base = {
            'KATO_SETTINGS_FILE': str(self.settings_path),
        }
        if extra:
            base.update(extra)
        return base

    def _fully_valid_settings(self) -> dict[str, str]:
        """A settings.json payload that satisfies the whole ``mode='all'``
        check for the default youtrack + openhands stack — chosen so a
        non-bedrock model (``gpt-4``) only needs a plain LLM api key."""
        return {
            'YOUTRACK_API_BASE_URL': 'https://youtrack.example',
            'YOUTRACK_API_TOKEN': 'yt-token',
            'YOUTRACK_PROJECT': 'PROJ',
            'YOUTRACK_ASSIGNEE': 'me',
            'REPOSITORY_ROOT_PATH': str(self.projects),
            'OPENHANDS_BASE_URL': 'https://openhands.example',
            'OPENHANDS_API_KEY': 'oh-key',
            'OH_SECRET_KEY': 'oh-secret',
            'OPENHANDS_LLM_MODEL': 'gpt-4',
            'OPENHANDS_LLM_API_KEY': 'llm-key',
        }

    def _get_status(self, *, extra_env=None, needs_config=False):
        """Hit ``/api/config-status`` with a clean, isolated env."""
        with patch.dict(os.environ, self._env(extra_env), clear=False):
            for key in _REQUIRED_KEYS:
                if not extra_env or key not in extra_env:
                    os.environ.pop(key, None)
            app = create_app(
                session_manager=_FakeManager(), needs_config=needs_config,
            )
            response = app.test_client().get('/api/config-status')
        self.assertEqual(response.status_code, 200)
        return response.get_json()


class ConfigStatusUnconfiguredTests(_Base):

    def test_needs_config_true_and_lists_missing_when_nothing_set(self) -> None:
        body = self._get_status()
        self.assertTrue(body['needs_config'])
        self.assertTrue(body['missing'])
        # The list names the concrete keys so the UI can render them.
        joined = '\n'.join(body['missing'])
        self.assertIn('YOUTRACK_API_TOKEN', joined)
        self.assertIn('OH_SECRET_KEY', joined)

    def test_setup_mode_reflects_boot_flag_true(self) -> None:
        # Booted unconfigured → the process is in setup mode.
        body = self._get_status(needs_config=True)
        self.assertTrue(body['setup_mode'])

    def test_setup_mode_false_by_default(self) -> None:
        # A normal (configured) boot does not flag setup mode.
        body = self._get_status(needs_config=False)
        self.assertFalse(body['setup_mode'])

    def test_setup_error_empty_by_default(self) -> None:
        body = self._get_status(needs_config=True)
        self.assertEqual(body['setup_error'], '')

    def test_setup_error_reflects_a_failed_start_attempt(self) -> None:
        # main's setup wait loop publishes the failure on the LIVE Flask
        # config (SETUP_ERROR); the wizard shows it instead of "all set".
        with patch.dict(os.environ, self._env(), clear=False):
            for key in _REQUIRED_KEYS:
                os.environ.pop(key, None)
            app = create_app(session_manager=_FakeManager(), needs_config=True)
            app.config['SETUP_ERROR'] = 'startup dependency validation failed: youtrack'
            response = app.test_client().get('/api/config-status')
        body = response.get_json()
        self.assertEqual(
            body['setup_error'],
            'startup dependency validation failed: youtrack',
        )


class ConfigStatusLayeredResolutionTests(_Base):

    def test_settings_json_value_clears_its_missing_item_without_restart(self) -> None:
        # Baseline: the token is reported missing.
        before = self._get_status()
        self.assertTrue(
            any('YOUTRACK_API_TOKEN' in item for item in before['missing']),
            before['missing'],
        )
        # Save it to settings.json only — no env change, no restart.
        self.settings_path.write_text(
            json.dumps({'YOUTRACK_API_TOKEN': 'saved-in-ui'}),
            encoding='utf-8',
        )
        after = self._get_status()
        # That specific requirement is now satisfied from settings.json.
        self.assertFalse(
            any('YOUTRACK_API_TOKEN' in item for item in after['missing']),
            after['missing'],
        )

    def test_fully_configured_via_settings_json_flips_needs_config_false(self) -> None:
        # The whole config saved through the UI (settings.json) is enough to
        # be "configured" — proving the operator never has to touch a
        # terminal or restart to satisfy the check.
        self.settings_path.write_text(
            json.dumps(self._fully_valid_settings()), encoding='utf-8',
        )
        body = self._get_status()
        self.assertEqual(body['missing'], [])
        self.assertFalse(body['needs_config'])

    def test_live_env_overrides_settings_toward_configured(self) -> None:
        # settings.json is missing the token; a live env var supplies it.
        settings = self._fully_valid_settings()
        settings.pop('YOUTRACK_API_TOKEN')
        self.settings_path.write_text(json.dumps(settings), encoding='utf-8')
        # Without the env var → missing.
        self.assertTrue(self._get_status()['needs_config'])
        # With it live in the process → configured, no restart.
        body = self._get_status(extra_env={'YOUTRACK_API_TOKEN': 'from-shell'})
        self.assertFalse(body['needs_config'])
        self.assertEqual(body['missing'], [])


if __name__ == '__main__':
    unittest.main()
