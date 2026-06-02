"""Tests for the Claude model catalog (alias IDs + best-effort live labels)."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from claude_core_lib.claude_core_lib.helpers import model_catalog


def _urlopen_returning(models):
    """A urlopen context-manager mock whose body is ``{"data": models}``."""
    payload = json.dumps({'data': models}).encode('utf-8')
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = payload
    return cm


_NO_CREDS = {'ANTHROPIC_API_KEY': '', 'CLAUDE_CODE_OAUTH_TOKEN': ''}


class ModelCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        model_catalog.reset_models_cache()
        self.addCleanup(model_catalog.reset_models_cache)
        # Default to no credential so a test never makes a real network call.
        ctx = patch.dict(os.environ, _NO_CREDS, clear=False)
        ctx.start()
        self.addCleanup(ctx.stop)

    def test_ids_are_stable_aliases_with_sonnet_default(self) -> None:
        models = model_catalog.discover_models()
        self.assertEqual([m['id'] for m in models], ['opus', 'sonnet', 'haiku'])
        self.assertEqual([m['id'] for m in models if m.get('default')], ['sonnet'])

    def test_no_credential_yields_versionless_labels_and_no_network(self) -> None:
        with patch('urllib.request.urlopen') as urlopen:
            models = model_catalog.discover_models()
            urlopen.assert_not_called()  # no creds → never hit the API
        self.assertEqual([m['label'] for m in models], ['Opus', 'Sonnet', 'Haiku'])

    def test_api_key_enriches_labels_with_latest_version(self) -> None:
        rows = [
            {'id': 'claude-opus-4-8', 'display_name': 'Claude Opus 4.8'},
            {'id': 'claude-opus-4-7', 'display_name': 'Claude Opus 4.7'},  # older, ignored
            {'id': 'claude-sonnet-4-6', 'display_name': 'Claude Sonnet 4.6'},
            {'id': 'claude-haiku-4-5-20251001', 'display_name': 'Claude Haiku 4.5'},
        ]
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-test'}), \
                patch('urllib.request.urlopen', return_value=_urlopen_returning(rows)):
            model_catalog.reset_models_cache()
            models = model_catalog.discover_models()
        labels = {m['id']: m['label'] for m in models}
        self.assertEqual(
            labels, {'opus': 'Opus 4.8', 'sonnet': 'Sonnet 4.6', 'haiku': 'Haiku 4.5'},
        )

    def test_non_dict_api_entries_are_skipped(self) -> None:
        rows = [None, 'bad', {'id': 'claude-opus-4-8', 'display_name': 'Claude Opus 4.8'}]
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-test'}), \
                patch('urllib.request.urlopen', return_value=_urlopen_returning(rows)):
            model_catalog.reset_models_cache()
            models = model_catalog.discover_models()
        labels = {m['id']: m['label'] for m in models}
        self.assertEqual(labels['opus'], 'Opus 4.8')
        self.assertEqual(labels['sonnet'], 'Sonnet')  # absent from API → version-less

    def test_api_failure_falls_back_to_versionless_labels(self) -> None:
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-test'}), \
                patch('urllib.request.urlopen', side_effect=OSError('boom')):
            model_catalog.reset_models_cache()
            models = model_catalog.discover_models()
        self.assertEqual([m['label'] for m in models], ['Opus', 'Sonnet', 'Haiku'])

    def test_result_is_cached(self) -> None:
        with patch.object(
            model_catalog, '_aliases_with_live_labels',
            return_value=[{'id': 'sonnet', 'label': 'Sonnet 4.6', 'default': True}],
        ) as builder:
            first = model_catalog.discover_models()
            second = model_catalog.discover_models()
        builder.assert_called_once()  # second call served from cache
        self.assertEqual(first, second)

    def test_returned_list_is_a_copy(self) -> None:
        # Mutating the result must not corrupt the cache.
        models = model_catalog.discover_models()
        models[0]['label'] = 'mutated'
        self.assertEqual(model_catalog.discover_models()[0]['label'], 'Opus')

    def test_auth_headers_prefers_api_key(self) -> None:
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk', 'CLAUDE_CODE_OAUTH_TOKEN': 'tok'}):
            self.assertEqual(model_catalog._auth_headers(), {'x-api-key': 'sk'})

    def test_auth_headers_uses_oauth_bearer_when_no_key(self) -> None:
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': '', 'CLAUDE_CODE_OAUTH_TOKEN': 'tok'}):
            self.assertEqual(model_catalog._auth_headers(), {'Authorization': 'Bearer tok'})

    def test_auth_headers_none_when_no_credential(self) -> None:
        with patch.dict(os.environ, _NO_CREDS):
            self.assertIsNone(model_catalog._auth_headers())

    def test_strip_claude_prefix(self) -> None:
        self.assertEqual(model_catalog._strip_claude_prefix('Claude Opus 4.8'), 'Opus 4.8')
        self.assertEqual(model_catalog._strip_claude_prefix('Opus 4.8'), 'Opus 4.8')


if __name__ == '__main__':
    unittest.main()
