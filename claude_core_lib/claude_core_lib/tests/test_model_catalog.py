"""Tests for the Claude model catalog (alias IDs + best-effort live labels)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
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
        # Isolate from the host's real ~/.claude logs so the session-log fallback
        # finds nothing by default — these tests assert the no-version baseline.
        # Individual session-log tests point CLAUDE_CONFIG_DIR at their own fixture.
        self._config_dir = tempfile.mkdtemp(prefix='kato-claude-cfg-')
        self.addCleanup(lambda: __import__('shutil').rmtree(self._config_dir, ignore_errors=True))
        creds = dict(_NO_CREDS, CLAUDE_CONFIG_DIR=self._config_dir)
        ctx = patch.dict(os.environ, creds, clear=False)
        ctx.start()
        self.addCleanup(ctx.stop)

    def _write_session_log(self, name: str, lines: list) -> Path:
        """Write a JSONL session log under the fixture ``projects/<enc>/`` dir."""
        project = Path(self._config_dir) / 'projects' / 'some-project'
        project.mkdir(parents=True, exist_ok=True)
        path = project / name
        with path.open('w', encoding='utf-8') as handle:
            for line in lines:
                handle.write((line if isinstance(line, str) else json.dumps(line)) + '\n')
        return path

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

    def test_cache_refreshes_after_ttl(self) -> None:
        # A new version released mid-process must surface without a restart: once
        # the TTL lapses, discovery recomputes instead of serving the stale label.
        calls = {'n': 0}

        def builder():
            calls['n'] += 1
            return [{'id': 'opus', 'label': f'Opus 4.{calls["n"]}'}]

        with patch.object(model_catalog, '_aliases_with_live_labels', side_effect=builder):
            first = model_catalog.discover_models()
            # Age the cache past its TTL.
            with model_catalog._cache_lock:
                model_catalog._cache_stamp -= model_catalog._CACHE_TTL_SECONDS + 1
            second = model_catalog.discover_models()
        self.assertEqual(calls['n'], 2)  # recomputed, not served from the stale cache
        self.assertNotEqual(first[0]['label'], second[0]['label'])

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

    # ----- credential-free version from the CLI's own session logs -----

    def test_session_logs_supply_versions_without_a_credential(self) -> None:
        # No API creds, but the CLI already resolved aliases on disk.
        self._write_session_log('s1.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-opus-4-8'}},
            {'type': 'assistant', 'message': {'model': 'claude-sonnet-4-6'}},
        ])
        with patch('urllib.request.urlopen') as urlopen:
            models = model_catalog.discover_models()
            urlopen.assert_not_called()  # still no creds → no network
        labels = {m['id']: m['label'] for m in models}
        self.assertEqual(labels['opus'], 'Opus 4.8')
        self.assertEqual(labels['sonnet'], 'Sonnet 4.6')
        self.assertEqual(labels['haiku'], 'Haiku')  # no haiku run on disk → version-less

    def test_api_label_wins_over_session_log_label(self) -> None:
        self._write_session_log('s1.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-opus-4-7'}},  # stale on disk
        ])
        rows = [{'id': 'claude-opus-4-8', 'display_name': 'Claude Opus 4.8'}]
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-test'}), \
                patch('urllib.request.urlopen', return_value=_urlopen_returning(rows)):
            model_catalog.reset_models_cache()
            models = model_catalog.discover_models()
        labels = {m['id']: m['label'] for m in models}
        self.assertEqual(labels['opus'], 'Opus 4.8')  # API authoritative, not the disk 4.7

    def test_newest_log_wins_for_a_family(self) -> None:
        old = self._write_session_log('old.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-opus-4-7'}},
        ])
        new = self._write_session_log('new.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-opus-4-8'}},
        ])
        os.utime(old, (1_000, 1_000))
        os.utime(new, (2_000, 2_000))
        labels = model_catalog._labels_from_session_logs()
        self.assertEqual(labels['opus'], 'Opus 4.8')

    def test_session_logs_skip_synthetic_and_corrupt_lines(self) -> None:
        self._write_session_log('s.jsonl', [
            'not json at all',
            {'type': 'assistant', 'message': {'model': '<synthetic>'}},
            {'type': 'system', 'model': 'claude-haiku-4-5-20251001'},  # top-level model
        ])
        labels = model_catalog._labels_from_session_logs()
        self.assertEqual(labels, {'haiku': 'Haiku 4.5'})  # date suffix dropped

    def test_session_logs_missing_dir_is_empty(self) -> None:
        # setUp's fixture has no projects/ dir yet → no logs, no error.
        self.assertEqual(model_catalog._labels_from_session_logs(), {})

    def test_unreadable_log_is_skipped(self) -> None:
        good = self._write_session_log('good.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-opus-4-8'}},
        ])
        os.utime(good, (1_000, 1_000))
        bad = self._write_session_log('bad.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-sonnet-4-6'}},
        ])
        os.utime(bad, (2_000, 2_000))  # newest, but made unreadable
        bad.chmod(0o000)
        self.addCleanup(lambda: bad.chmod(0o644))
        labels = model_catalog._labels_from_session_logs()
        self.assertEqual(labels.get('opus'), 'Opus 4.8')  # good one still read

    def test_family_label_from_model_id(self) -> None:
        self.assertEqual(
            model_catalog._family_label_from_model_id('claude-opus-4-8'), ('opus', 'Opus 4.8'),
        )
        self.assertEqual(
            model_catalog._family_label_from_model_id('claude-haiku-4-5-20251001'),
            ('haiku', 'Haiku 4.5'),
        )
        self.assertIsNone(model_catalog._family_label_from_model_id('<synthetic>'))
        self.assertIsNone(model_catalog._family_label_from_model_id(''))
        self.assertIsNone(model_catalog._family_label_from_model_id('gpt-4o'))

    def test_family_label_handles_future_version_shapes(self) -> None:
        # A future release (new minor, double-digit minor, new major, or no minor)
        # must still parse — the label tracks whatever the CLI resolves to.
        cases = {
            'claude-opus-4-9': ('opus', 'Opus 4.9'),
            'claude-opus-4-10': ('opus', 'Opus 4.10'),
            'claude-sonnet-5-0': ('sonnet', 'Sonnet 5.0'),
            'claude-opus-5': ('opus', 'Opus 5'),  # no minor yet
        }
        for model_id, expected in cases.items():
            self.assertEqual(model_catalog._family_label_from_model_id(model_id), expected)
        # A brand-new FAMILY (new model name) isn't one of the three CLI aliases,
        # so it is deliberately not labelled here — it can't be selected anyway.
        self.assertIsNone(model_catalog._family_label_from_model_id('claude-neptune-1-0'))

    def test_model_id_of_event_handles_both_shapes_and_junk(self) -> None:
        self.assertEqual(model_catalog._model_id_of_event({'model': 'x'}), 'x')
        self.assertEqual(
            model_catalog._model_id_of_event({'message': {'model': 'y'}}), 'y',
        )
        self.assertEqual(model_catalog._model_id_of_event({'message': 'notadict'}), '')
        self.assertEqual(model_catalog._model_id_of_event({}), '')
        self.assertEqual(model_catalog._model_id_of_event('notadict'), '')


if __name__ == '__main__':
    unittest.main()
