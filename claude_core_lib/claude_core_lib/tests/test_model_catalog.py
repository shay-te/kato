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
        # Isolate from the host's real ~/.claude logs AND ~/.claude.json so the
        # credential-free fallbacks find nothing by default — these tests assert
        # the no-version baseline. Individual tests write their own fixtures
        # under this dir.
        self._config_dir = tempfile.mkdtemp(prefix='the orchestrator-claude-cfg-')
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

    def _write_cli_config(self, payload) -> Path:
        """Write the fixture ``.claude.json`` the CLI-config source reads."""
        path = Path(self._config_dir) / '.claude.json'
        path.write_text(
            payload if isinstance(payload, str) else json.dumps(payload),
            encoding='utf-8',
        )
        return path

    def test_ids_are_stable_aliases_with_sonnet_default(self) -> None:
        # Fable is gated (offered only when discovery confirms access — see
        # below), so the baseline catalog is the always-resolvable CLI aliases.
        models = model_catalog.discover_models()
        self.assertEqual(
            [m['id'] for m in models], ['opus', 'sonnet', 'haiku'],
        )
        self.assertEqual([m['id'] for m in models if m.get('default')], ['sonnet'])

    def test_ids_never_leak_internal_keys(self) -> None:
        # ``family``/``gated`` are discovery bookkeeping — the picker payload
        # must carry only id/label/default.
        for model in model_catalog.discover_models():
            self.assertEqual(set(model) - {'id', 'label', 'default'}, set())

    def test_fable_is_offered_as_the_cli_alias_when_available(self) -> None:
        # ``--model fable`` is a real CLI alias ("Provide an alias for the latest
        # model (e.g. 'fable', 'opus', or 'sonnet')"), so the picker offers the
        # ALIAS — never a pinned full id that can drift from what spawn runs.
        # It stays gated: only shown once discovery confirms account access.
        rows = [{'id': 'claude-fable-5', 'display_name': 'Claude Fable 5'}]
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-test'}), \
                patch('urllib.request.urlopen', return_value=_urlopen_returning(rows)):
            model_catalog.reset_models_cache()
            models = {m['id']: m for m in model_catalog.discover_models()}
        self.assertEqual(models['fable']['label'], 'Fable 5')

    def test_fable_is_hidden_when_discovery_cannot_confirm_it(self) -> None:
        # No credential + no CLI config + no session log mentioning fable →
        # fable is NOT offered; picking a model the account lacks errors
        # ("Claude Fable 5 is currently unavailable").
        ids = [m['id'] for m in model_catalog.discover_models()]
        self.assertNotIn('fable', ids)
        self.assertEqual(ids, ['opus', 'sonnet', 'haiku'])

    def test_no_credential_yields_versionless_labels_and_no_network(self) -> None:
        with patch('urllib.request.urlopen') as urlopen:
            models = model_catalog.discover_models()
            urlopen.assert_not_called()  # no creds → never hit the API
        # Fable gated out (no discovery to confirm it); stable aliases remain.
        self.assertEqual(
            [m['label'] for m in models], ['Opus', 'Sonnet', 'Haiku'],
        )

    def test_api_key_enriches_labels_with_latest_version(self) -> None:
        rows = [
            {'id': 'claude-fable-5', 'display_name': 'Claude Fable 5'},
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
            labels,
            {'fable': 'Fable 5', 'opus': 'Opus 4.8',
             'sonnet': 'Sonnet 4.6', 'haiku': 'Haiku 4.5'},
        )

    def test_api_ordering_does_not_decide_the_label(self) -> None:
        # The API's ordering is not contractual. Listing an OLDER model first
        # must not win — highest version does, or the picker silently
        # downgrades ("Opus 4.8" while Opus 5 is live).
        rows = [
            {'id': 'claude-opus-4-8', 'display_name': 'Claude Opus 4.8'},
            {'id': 'claude-opus-5', 'display_name': 'Claude Opus 5'},
        ]
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-test'}), \
                patch('urllib.request.urlopen', return_value=_urlopen_returning(rows)):
            model_catalog.reset_models_cache()
            models = {m['id']: m['label'] for m in model_catalog.discover_models()}
        self.assertEqual(models['opus'], 'Opus 5')

    def test_a_newer_fable_moves_the_label_but_never_the_id(self) -> None:
        # When a newer fable ships the LABEL follows; the id stays the alias,
        # so the option can never name a model different from what spawn runs.
        rows = [{'id': 'claude-fable-6', 'display_name': 'Claude Fable 6'}]
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-test'}), \
                patch('urllib.request.urlopen', return_value=_urlopen_returning(rows)):
            model_catalog.reset_models_cache()
            models = model_catalog.discover_models()
        fable = next(m for m in models if m['id'] == 'fable')
        self.assertEqual(fable['label'], 'Fable 6')

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
        # API failed + no CLI config + no session logs → fable can't be
        # confirmed → gated out.
        self.assertEqual(
            [m['label'] for m in models], ['Opus', 'Sonnet', 'Haiku'],
        )

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

    # ----- credential-free labels from the CLI's OWN config cache -----

    def test_cli_config_supplies_labels_without_a_credential_or_a_prior_run(self) -> None:
        # The whole point of this source: the CLI caches the model options the
        # SERVER offered this account, so a model is discoverable before it has
        # ever been run here. Without it, a no-credential host could only ever
        # see models it had already used — and gated fable, which it could not
        # use until it appeared, could never appear at all.
        self._write_cli_config({
            'additionalModelOptionsCache': [
                {'value': 'claude-fable-5[1m]', 'label': 'Fable'},
            ],
            'clientDataCacheSlots': {
                'bi1-aaa': {'model': 'claude-opus-5'},
                'bi1-bbb': {'model': 'claude-opus-4-8'},  # older, must not win
            },
        })
        with patch('urllib.request.urlopen') as urlopen:
            models = model_catalog.discover_models()
            urlopen.assert_not_called()  # no creds → no network
        labels = {m['id']: m['label'] for m in models}
        self.assertEqual(labels['fable'], 'Fable 5')  # gated tier now offered
        self.assertEqual(labels['opus'], 'Opus 5')
        self.assertEqual(labels['sonnet'], 'Sonnet')  # not in the cache → version-less

    def test_api_label_wins_over_the_cli_config(self) -> None:
        self._write_cli_config({
            'clientDataCacheSlots': {'s': {'model': 'claude-opus-4-7'}},
        })
        rows = [{'id': 'claude-opus-4-8', 'display_name': 'Claude Opus 4.8'}]
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-test'}), \
                patch('urllib.request.urlopen', return_value=_urlopen_returning(rows)):
            model_catalog.reset_models_cache()
            labels = {m['id']: m['label'] for m in model_catalog.discover_models()}
        self.assertEqual(labels['opus'], 'Opus 4.8')

    def test_cli_config_wins_over_session_logs(self) -> None:
        # The config reflects what the account may run NOW; a session log only
        # proves what it ran once.
        self._write_session_log('s.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-opus-4-8'}},
        ])
        self._write_cli_config({
            'clientDataCacheSlots': {'s': {'model': 'claude-opus-5'}},
        })
        labels = {m['id']: m['label'] for m in model_catalog.discover_models()}
        self.assertEqual(labels['opus'], 'Opus 5')

    def test_cli_config_missing_or_corrupt_is_silent(self) -> None:
        # No file at all (setUp's fixture dir is empty).
        self.assertEqual(model_catalog._labels_from_cli_config(), {})
        # Unparseable.
        self._write_cli_config('{not json')
        self.assertEqual(model_catalog._labels_from_cli_config(), {})
        # Valid JSON but not an object.
        self._write_cli_config([1, 2, 3])
        self.assertEqual(model_catalog._labels_from_cli_config(), {})

    def test_cli_config_reads_the_per_project_usage_ledger(self) -> None:
        # A third place the CLI records models, covering families the option
        # cache and the client-data slots miss — free, since the file is
        # already parsed, and it keeps the log walk from being needed at all.
        self._write_cli_config({'projects': {
            '/work/a': {'lastModelUsage': {'claude-haiku-4-5-20251001': {}}},
            '/work/b': {'lastModelUsage': {'claude-sonnet-5': {}}},
            '/work/c': {'lastModelUsage': {}},
            '/work/d': 'not-a-dict',
        }})
        self.assertEqual(
            model_catalog._labels_from_cli_config(),
            {'haiku': 'Haiku 4.5', 'sonnet': 'Sonnet 5'},
        )

    def test_cli_config_junk_shapes_are_skipped(self) -> None:
        # The CLI's private on-disk shape can change under us — every access is
        # defensive, so junk degrades to "no labels", never an exception.
        self._write_cli_config({
            'additionalModelOptionsCache': 'not-a-list',
            'clientDataCacheSlots': [
                {'model': 'claude-opus-5'},  # a LIST, not the expected dict
            ],
        })
        self.assertEqual(model_catalog._labels_from_cli_config(), {})
        self._write_cli_config({
            'additionalModelOptionsCache': [None, 'bad', {'value': 'gpt-4o'},
                                            {'value': 'claude-opus-5'}],
            'clientDataCacheSlots': {'a': None, 'b': {'model': None}},
        })
        self.assertEqual(model_catalog._labels_from_cli_config(), {'opus': 'Opus 5'})

    def test_full_coverage_skips_the_expensive_log_scan(self) -> None:
        # The log scan opens hundreds of files (~1-2s on a busy host). Once the
        # cheaper sources have answered for every family there is nothing left
        # for it to contribute, so it must not run at all.
        self._write_cli_config({'clientDataCacheSlots': {
            'a': {'model': 'claude-fable-5'}, 'b': {'model': 'claude-opus-5'},
            'c': {'model': 'claude-sonnet-5'}, 'd': {'model': 'claude-haiku-4-5'},
        }})
        with patch.object(model_catalog, '_labels_from_session_logs') as scan:
            labels = {m['id']: m['label'] for m in model_catalog.discover_models()}
            scan.assert_not_called()
        self.assertEqual(labels['opus'], 'Opus 5')

    def test_partial_coverage_still_falls_through_to_the_log_scan(self) -> None:
        self._write_cli_config({
            'clientDataCacheSlots': {'a': {'model': 'claude-opus-5'}},
        })
        self._write_session_log('s.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-haiku-4-5-20251001'}},
        ])
        labels = {m['id']: m['label'] for m in model_catalog.discover_models()}
        self.assertEqual(labels['opus'], 'Opus 5')     # from the config
        self.assertEqual(labels['haiku'], 'Haiku 4.5')  # from the logs

    def test_cli_config_path_follows_the_config_dir_override(self) -> None:
        # ~/.claude.json is a SIBLING of ~/.claude, not inside it.
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': '/tmp/cfg'}):
            self.assertEqual(
                model_catalog._cli_config_path(), Path('/tmp/cfg/.claude.json'),
            )
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': ''}):
            self.assertEqual(
                model_catalog._cli_config_path(), Path.home() / '.claude.json',
            )

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

    def test_highest_version_wins_for_a_family(self) -> None:
        old = self._write_session_log('old.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-opus-4-7'}},
        ])
        new = self._write_session_log('new.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-opus-4-8'}},
        ])
        os.utime(old, (1_000, 1_000))
        os.utime(new, (2_000, 2_000))
        self.assertEqual(
            model_catalog._labels_from_session_logs()['opus'], 'Opus 4.8',
        )

    def test_resuming_an_old_version_does_not_downgrade_the_label(self) -> None:
        # Regression for the "still shows Opus 4.7" incident: an OLD 4.7 session
        # that gets resumed/re-touched becomes the newest-mtime log, but 4.8 has
        # already been run — the label must stay 4.8 (highest version), not follow
        # mtime back down to 4.7.
        ran_48 = self._write_session_log('ran48.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-opus-4-8'}},
        ])
        resumed_47 = self._write_session_log('resumed47.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-opus-4-7'}},
        ])
        os.utime(ran_48, (1_000, 1_000))       # older
        os.utime(resumed_47, (9_000, 9_000))   # newest mtime, but lower version
        self.assertEqual(
            model_catalog._labels_from_session_logs()['opus'], 'Opus 4.8',
        )

    def test_a_new_major_beats_a_high_minor(self) -> None:
        # The reported bug: the picker read "Opus 4.8" off the logs while
        # Opus 5 was live. (5, 0) must outrank (4, 8).
        self._write_session_log('a.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-opus-4-8'}},
        ])
        self._write_session_log('b.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-opus-5'}},
        ])
        self.assertEqual(
            model_catalog._labels_from_session_logs()['opus'], 'Opus 5',
        )

    def test_double_digit_minor_beats_single_digit(self) -> None:
        a = self._write_session_log('a.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-opus-4-9'}},
        ])
        b = self._write_session_log('b.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-opus-4-10'}},
        ])
        os.utime(a, (9_000, 9_000))   # 4.9 is newer by mtime
        os.utime(b, (1_000, 1_000))   # 4.10 is older by mtime but higher version
        self.assertEqual(
            model_catalog._labels_from_session_logs()['opus'], 'Opus 4.10',
        )

    def test_session_logs_skip_synthetic_and_corrupt_lines(self) -> None:
        self._write_session_log('s.jsonl', [
            'not json at all',
            {'type': 'assistant', 'message': {'model': '<synthetic>'}},
            {'type': 'system', 'model': 'claude-haiku-4-5-20251001'},  # top-level model
        ])
        # Date suffix dropped from the label.
        self.assertEqual(
            model_catalog._labels_from_session_logs(), {'haiku': 'Haiku 4.5'},
        )

    def test_session_logs_confirm_fable_without_a_credential(self) -> None:
        # A fable run recorded by the CLI (with the context-window marker the CLI
        # logs) is enough to un-gate the fable alias in the picker.
        self._write_session_log('f.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-fable-5[1m]'}},
        ])
        self.assertEqual(
            model_catalog._labels_from_session_logs()['fable'], 'Fable 5',
        )
        self.assertIn('fable', [m['id'] for m in model_catalog.discover_models()])

    def test_a_huge_log_is_not_read_past_its_head(self) -> None:
        # A single-model session never satisfies the all-families stop
        # condition, so before the byte cap it was read to EOF — tens of MB per
        # transcript. The id is on the opening turn; everything after it was
        # pure I/O.
        filler = json.dumps({'type': 'user', 'content': 'x' * 4000})
        self._write_session_log('big.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-opus-5'}},
            *[filler] * 200,
            # Past the cap — proof the tail was never reached.
            {'type': 'assistant', 'message': {'model': 'claude-sonnet-5'}},
        ])
        with patch.object(model_catalog, '_LOG_HEAD_BYTES', 8 * 1024):
            labels = model_catalog._labels_from_session_logs()
        self.assertEqual(labels, {'opus': 'Opus 5'})

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

    def test_family_version_returns_numeric_major_minor(self) -> None:
        self.assertEqual(
            model_catalog.family_version_from_model_id('claude-opus-4-8'),
            ('opus', 4, 8, 'Opus 4.8'),
        )
        self.assertEqual(
            model_catalog.family_version_from_model_id('claude-haiku-4-5-20251001'),
            ('haiku', 4, 5, 'Haiku 4.5'),
        )
        # No minor → sorts as 0 but the label stays "Opus 5".
        self.assertEqual(
            model_catalog.family_version_from_model_id('claude-opus-5'),
            ('opus', 5, 0, 'Opus 5'),
        )
        self.assertEqual(
            model_catalog.family_version_from_model_id('claude-fable-5'),
            ('fable', 5, 0, 'Fable 5'),
        )
        self.assertIsNone(model_catalog.family_version_from_model_id('<synthetic>'))
        self.assertIsNone(model_catalog.family_version_from_model_id(''))
        self.assertIsNone(model_catalog.family_version_from_model_id('gpt-4o'))

    def test_family_version_handles_future_version_shapes(self) -> None:
        # A future release (new minor, double-digit minor, new major, or no minor)
        # must still parse — the label tracks whatever the CLI resolves to.
        cases = {
            'claude-opus-4-9': 'Opus 4.9',
            'claude-opus-4-10': 'Opus 4.10',
            'claude-sonnet-5-0': 'Sonnet 5.0',
            'claude-opus-5': 'Opus 5',  # no minor yet
        }
        for model_id, expected in cases.items():
            self.assertEqual(
                model_catalog.family_version_from_model_id(model_id)[3], expected,
            )
        # A brand-new FAMILY (new model name) isn't one of the selectable
        # families, so it is deliberately not labelled — it can't be selected.
        self.assertIsNone(model_catalog.family_version_from_model_id('claude-neptune-1-0'))

    def test_date_after_no_minor_major_is_not_parsed_as_the_minor(self) -> None:
        # Real historical ids exist with NO minor and a date right after the
        # major (claude-sonnet-4-20250514). Parsing the date as the minor
        # would make (4, 20250514) outrank every genuine 4.x in the
        # highest-version comparison and garble the label.
        self.assertEqual(
            model_catalog.family_version_from_model_id('claude-sonnet-4-20250514'),
            ('sonnet', 4, 0, 'Sonnet 4'),
        )
        self.assertEqual(
            model_catalog.family_version_from_model_id('claude-fable-5-20260301'),
            ('fable', 5, 0, 'Fable 5'),
        )

    def test_old_dated_no_minor_log_does_not_outrank_a_real_minor(self) -> None:
        # A resumed pre-2025-Q3 transcript (claude-sonnet-4-20250514) in the
        # scan window must not beat the genuinely newer sonnet 4.6 — the
        # "Sonnet 4.20250514" garbled-label regression.
        self._write_session_log('old.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-sonnet-4-20250514'}},
        ])
        self._write_session_log('new.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-sonnet-4-6'}},
        ])
        self.assertEqual(
            model_catalog._labels_from_session_logs()['sonnet'], 'Sonnet 4.6',
        )

    def test_dated_fable_snapshot_labels_as_the_plain_version(self) -> None:
        # A dated fable snapshot id must surface as plain "Fable 5" — not as
        # "Fable 5.20260301" shadowing genuine 5.x releases.
        self._write_session_log('f.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-fable-5-20260301'}},
        ])
        self.assertEqual(
            model_catalog._labels_from_session_logs()['fable'], 'Fable 5',
        )

    def test_model_id_of_event_handles_both_shapes_and_junk(self) -> None:
        self.assertEqual(model_catalog._model_id_of_event({'model': 'x'}), 'x')
        self.assertEqual(
            model_catalog._model_id_of_event({'message': {'model': 'y'}}), 'y',
        )
        self.assertEqual(model_catalog._model_id_of_event({'message': 'notadict'}), '')
        self.assertEqual(model_catalog._model_id_of_event({}), '')
        self.assertEqual(model_catalog._model_id_of_event('notadict'), '')

    def test_fallback_models_are_the_ungated_aliases(self) -> None:
        # Total discovery failure still renders a picker — of models every
        # account can actually resolve.
        self.assertEqual(
            [m['id'] for m in model_catalog.FALLBACK_MODELS],
            ['opus', 'sonnet', 'haiku'],
        )


if __name__ == '__main__':
    unittest.main()
