"""Tests for the live OpenRouter model-catalogue discovery."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from kato_core_lib.helpers import openrouter_model_discovery as disc


def _urlopen_returning(payload):
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = json.dumps(payload).encode('utf-8')
    return cm


class OpenRouterModelDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        disc.reset_openrouter_models_cache()
        self.addCleanup(disc.reset_openrouter_models_cache)

    def test_parses_catalog_with_prefix_and_name(self) -> None:
        rows = {'data': [
            {'id': 'openai/gpt-4o', 'name': 'OpenAI: GPT-4o'},
            {'id': 'anthropic/claude-opus-4.8', 'name': 'Anthropic: Claude Opus 4.8'},
        ]}
        with patch('urllib.request.urlopen', return_value=_urlopen_returning(rows)):
            models = disc.discover_openrouter_models()
        self.assertEqual(models, [
            {'id': 'openrouter/openai/gpt-4o', 'label': 'OpenAI: GPT-4o'},
            {'id': 'openrouter/anthropic/claude-opus-4.8',
             'label': 'Anthropic: Claude Opus 4.8'},
        ])

    def test_label_falls_back_to_slug_when_name_missing(self) -> None:
        rows = {'data': [{'id': 'x/y'}]}
        with patch('urllib.request.urlopen', return_value=_urlopen_returning(rows)):
            models = disc.discover_openrouter_models()
        self.assertEqual(models, [{'id': 'openrouter/x/y', 'label': 'x/y'}])

    def test_skips_bad_rows_and_dedupes(self) -> None:
        rows = {'data': [
            None,
            'notadict',
            {'name': 'no id'},
            {'id': '   '},
            {'id': 'a/b', 'name': 'First'},
            {'id': 'a/b', 'name': 'Dup ignored'},
        ]}
        with patch('urllib.request.urlopen', return_value=_urlopen_returning(rows)):
            models = disc.discover_openrouter_models()
        self.assertEqual(models, [{'id': 'openrouter/a/b', 'label': 'First'}])

    def test_network_failure_falls_back_to_static_set(self) -> None:
        with patch('urllib.request.urlopen', side_effect=OSError('offline')):
            models = disc.discover_openrouter_models()
        self.assertEqual(models, [dict(m) for m in disc.FALLBACK_OPENROUTER_MODELS])

    def test_non_dict_payload_falls_back(self) -> None:
        with patch('urllib.request.urlopen', return_value=_urlopen_returning(['not', 'a', 'dict'])):
            models = disc.discover_openrouter_models()
        self.assertEqual(models, [dict(m) for m in disc.FALLBACK_OPENROUTER_MODELS])

    def test_empty_data_falls_back(self) -> None:
        with patch('urllib.request.urlopen', return_value=_urlopen_returning({'data': []})):
            models = disc.discover_openrouter_models()
        self.assertEqual(models, [dict(m) for m in disc.FALLBACK_OPENROUTER_MODELS])

    def test_result_is_cached(self) -> None:
        rows = {'data': [{'id': 'a/b', 'name': 'AB'}]}
        with patch('urllib.request.urlopen', return_value=_urlopen_returning(rows)) as urlopen:
            disc.discover_openrouter_models()
            disc.discover_openrouter_models()
            urlopen.assert_called_once()  # second call served from cache

    def test_cache_refreshes_after_ttl(self) -> None:
        first_rows = {'data': [{'id': 'a/b', 'name': 'AB'}]}
        second_rows = {'data': [{'id': 'c/d', 'name': 'CD'}]}
        with patch('urllib.request.urlopen', return_value=_urlopen_returning(first_rows)):
            first = disc.discover_openrouter_models()
        # Age the cache past its TTL, then a refreshed catalogue is picked up.
        with disc._cache_lock:
            disc._cache_stamp -= disc._CACHE_TTL_SECONDS + 1
        with patch('urllib.request.urlopen', return_value=_urlopen_returning(second_rows)):
            second = disc.discover_openrouter_models()
        self.assertEqual(first[0]['id'], 'openrouter/a/b')
        self.assertEqual(second[0]['id'], 'openrouter/c/d')

    def test_returned_list_is_a_copy(self) -> None:
        with patch('urllib.request.urlopen', side_effect=OSError('offline')):
            models = disc.discover_openrouter_models()
        models[0]['label'] = 'mutated'
        with patch('urllib.request.urlopen', side_effect=OSError('offline')):
            again = disc.discover_openrouter_models()
        self.assertNotEqual(again[0]['label'], 'mutated')


if __name__ == '__main__':
    unittest.main()
