"""``GET /api/claude/models``: what the settings model fields suggest.

Report: a teammate on a smaller plan runs Sonnet 4.6, typed it into "Model
override", and the CLI said there is no such model. The field now offers the
aliases and every pinned version the account has — read here from a real
``.claude.json`` in a throwaway CLAUDE_CONFIG_DIR, no credentials, no network.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_core_lib.claude_core_lib.helpers import model_catalog
from kato_core_lib.helpers.kato_settings_schema_utils import SETTINGS_SCHEMA
from kato_webserver.app import create_app


class _FakeManager:
    def list_records(self):
        return []

    def get_record(self, task_id):  # noqa: ARG002
        return None

    def get_session(self, task_id):  # noqa: ARG002
        return None


class ClaudeModelsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='claude-models-')
        self.addCleanup(tmp.cleanup)
        self.config_dir = Path(tmp.name)
        env = {key: value for key, value in os.environ.items()
               if key not in ('ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN')}
        env['CLAUDE_CONFIG_DIR'] = str(self.config_dir)
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        model_catalog.reset_models_cache()
        self.addCleanup(model_catalog.reset_models_cache)
        self.client = create_app(session_manager=_FakeManager()).test_client()

    def test_the_aliases_then_every_version_older_ones_included(self) -> None:
        # A fresh install: nothing run, no credential — Sonnet 4.6 still offered.
        models = self.client.get('/api/claude/models').get_json()['models']
        ids = [model['id'] for model in models]
        self.assertEqual(ids[:3], ['opus', 'sonnet', 'haiku'])  # fable only once confirmed
        self.assertIn('claude-sonnet-4-6', ids)
        by_id = {model['id']: model for model in models}
        self.assertEqual(by_id['claude-sonnet-4-6']['label'], 'Sonnet 4.6')
        self.assertTrue(by_id['sonnet']['label'].endswith('— always the newest Sonnet'))
        self.assertEqual({model['group'] for model in models[:3]}, {'newest'})
        self.assertEqual({model['group'] for model in models[3:]}, {'version'})

    def test_a_version_the_account_has_beyond_the_known_ones_is_offered(self) -> None:
        (self.config_dir / '.claude.json').write_text(json.dumps({
            'additionalModelOptionsCache': [{'value': 'claude-sonnet-6-1'}],
        }), encoding='utf-8')
        ids = [model['id'] for model in self.client.get('/api/claude/models').get_json()['models']]
        self.assertIn('claude-sonnet-6-1', ids)

    def test_a_catalogue_that_breaks_still_answers_a_list(self) -> None:
        with patch.object(model_catalog, 'discover_pinned_models', side_effect=RuntimeError('boom')):
            response = self.client.get('/api/claude/models')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'models': []})

    def test_both_model_fields_are_pickers_with_a_claude_placeholder(self) -> None:
        fields = {field[0]: field[4] for section in SETTINGS_SCHEMA for field in section['fields']}
        for key in ('KATO_CLAUDE_MODEL', 'KATO_CLAUDE_FALLBACK_MODEL'):
            with self.subTest(key=key):
                self.assertEqual(fields[key]['picker'], 'claude')
                self.assertTrue(fields[key]['empty_label'].startswith('Not set'))
                self.assertIn('claude-', fields[key]['placeholder'])  # never "gpt-4"


if __name__ == '__main__':
    unittest.main()
