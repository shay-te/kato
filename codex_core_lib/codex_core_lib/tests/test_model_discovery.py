"""Tests for Codex model discovery from the CLI's models_cache.json."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_core_lib.codex_core_lib.helpers import model_discovery


def _cache(models):
    return json.dumps({'fetched_at': 'x', 'etag': 'W/"x"', 'models': models})


_SAMPLE = [
    {'slug': 'gpt-5.5', 'display_name': 'GPT-5.5', 'visibility': 'list',
     'supported_in_api': True, 'priority': 9},
    {'slug': 'gpt-5.4', 'display_name': 'GPT-5.4', 'visibility': 'list',
     'supported_in_api': True, 'priority': 16},
    {'slug': 'gpt-5.4-mini', 'display_name': 'GPT-5.4-Mini', 'visibility': 'list',
     'supported_in_api': True, 'priority': 23},
    # Hidden internal model — must be excluded.
    {'slug': 'codex-auto-review', 'display_name': 'Codex Auto Review',
     'visibility': 'hide', 'supported_in_api': True, 'priority': 43},
    # Listed but not API-supported — must be excluded.
    {'slug': 'gpt-legacy', 'display_name': 'GPT Legacy', 'visibility': 'list',
     'supported_in_api': False, 'priority': 5},
]


class CodexModelDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        model_discovery.reset_codex_models_cache()
        self.addCleanup(model_discovery.reset_codex_models_cache)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        ctx = patch.dict(os.environ, {'CODEX_HOME': str(self.home)})
        ctx.start()
        self.addCleanup(ctx.stop)

    def _write_cache(self, models) -> None:
        (self.home / 'models_cache.json').write_text(_cache(models), encoding='utf-8')

    def test_parses_listed_api_models_sorted_by_priority(self) -> None:
        self._write_cache(_SAMPLE)
        models = model_discovery.discover_codex_models()
        self.assertEqual(
            [m['id'] for m in models], ['gpt-5.5', 'gpt-5.4', 'gpt-5.4-mini'],
        )
        self.assertEqual(
            [m['label'] for m in models], ['GPT-5.5', 'GPT-5.4', 'GPT-5.4-Mini'],
        )

    def test_first_by_priority_is_default(self) -> None:
        self._write_cache(_SAMPLE)
        models = model_discovery.discover_codex_models()
        self.assertEqual([m['id'] for m in models if m.get('default')], ['gpt-5.5'])

    def test_hidden_and_non_api_models_excluded(self) -> None:
        self._write_cache(_SAMPLE)
        ids = [m['id'] for m in model_discovery.discover_codex_models()]
        self.assertNotIn('codex-auto-review', ids)
        self.assertNotIn('gpt-legacy', ids)

    def test_unsorted_input_is_ordered_by_priority(self) -> None:
        self._write_cache(list(reversed(_SAMPLE)))
        ids = [m['id'] for m in model_discovery.discover_codex_models()]
        self.assertEqual(ids, ['gpt-5.5', 'gpt-5.4', 'gpt-5.4-mini'])

    def test_missing_file_falls_back(self) -> None:
        models = model_discovery.discover_codex_models()
        self.assertEqual(list(models), list(model_discovery.FALLBACK_CODEX_MODELS))

    def test_malformed_json_falls_back(self) -> None:
        (self.home / 'models_cache.json').write_text('not json{', encoding='utf-8')
        models = model_discovery.discover_codex_models()
        self.assertEqual(list(models), list(model_discovery.FALLBACK_CODEX_MODELS))

    def test_models_field_not_a_list_falls_back(self) -> None:
        (self.home / 'models_cache.json').write_text('{"models": "nope"}', encoding='utf-8')
        models = model_discovery.discover_codex_models()
        self.assertEqual(list(models), list(model_discovery.FALLBACK_CODEX_MODELS))

    def test_no_listable_models_falls_back(self) -> None:
        self._write_cache([
            {'slug': 'hidden', 'display_name': 'H', 'visibility': 'hide',
             'supported_in_api': True, 'priority': 1},
        ])
        models = model_discovery.discover_codex_models()
        self.assertEqual(list(models), list(model_discovery.FALLBACK_CODEX_MODELS))

    def test_codex_home_override_is_honored(self) -> None:
        self.assertEqual(
            model_discovery.codex_models_cache_path(),
            self.home / 'models_cache.json',
        )

    def test_cache_refreshes_on_mtime_change(self) -> None:
        self._write_cache(_SAMPLE)
        first = model_discovery.discover_codex_models()
        self.assertEqual(len(first), 3)
        # Rewrite with a single model and a bumped mtime → re-read.
        os.utime(self.home / 'models_cache.json', ns=(2, 2))
        self._write_cache([
            {'slug': 'gpt-6', 'display_name': 'GPT-6', 'visibility': 'list',
             'supported_in_api': True, 'priority': 1},
        ])
        os.utime(self.home / 'models_cache.json', ns=(99, 99))
        second = model_discovery.discover_codex_models()
        self.assertEqual([m['id'] for m in second], ['gpt-6'])

    def test_result_is_a_copy(self) -> None:
        self._write_cache(_SAMPLE)
        models = model_discovery.discover_codex_models()
        models[0]['label'] = 'mutated'
        self.assertEqual(model_discovery.discover_codex_models()[0]['label'], 'GPT-5.5')


if __name__ == '__main__':
    unittest.main()
