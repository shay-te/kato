"""Unit tests for ``kato_settings_store``.

Pins the contract the whole settings-UI migration rests on:

* read/write round-trips through ``~/.kato/settings.json``
  (redirected via ``KATO_SETTINGS_FILE`` so tests never touch a real
  home dir);
* a corrupt / non-dict file degrades to ``{}`` instead of raising
  (a hand-edit typo must not brick boot);
* ``load_kato_settings_into_environ`` honours the precedence the
  boot path depends on: a real env var already in ``os.environ`` is
  NEVER overwritten (shell wins), but unset keys get populated.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.helpers.kato_settings_store import (
    kato_settings_path,
    load_kato_settings_into_environ,
    read_kato_settings,
    write_kato_settings,
)


class KatoSettingsStoreTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / 'settings.json'

    def _env(self, extra=None):
        base = {'KATO_SETTINGS_FILE': str(self.path)}
        if extra:
            base.update(extra)
        return base

    def test_path_honours_override(self) -> None:
        with patch.dict(os.environ, self._env()):
            self.assertEqual(kato_settings_path(), self.path)

    def test_read_missing_file_returns_empty(self) -> None:
        with patch.dict(os.environ, self._env()):
            self.assertEqual(read_kato_settings(), {})

    def test_write_then_read_round_trips(self) -> None:
        with patch.dict(os.environ, self._env()):
            write_kato_settings({'KATO_ISSUE_PLATFORM': 'jira'})
            self.assertEqual(
                read_kato_settings(), {'KATO_ISSUE_PLATFORM': 'jira'},
            )

    def test_write_merges_not_replaces(self) -> None:
        with patch.dict(os.environ, self._env()):
            write_kato_settings({'A': '1'})
            write_kato_settings({'B': '2'})
            self.assertEqual(read_kato_settings(), {'A': '1', 'B': '2'})

    def test_write_coerces_values_to_str(self) -> None:
        with patch.dict(os.environ, self._env()):
            write_kato_settings({'N': 5, 'B': True})
            saved = read_kato_settings()
        self.assertEqual(saved['N'], '5')
        self.assertEqual(saved['B'], 'True')

    def test_corrupt_file_degrades_to_empty(self) -> None:
        self.path.write_text('{ not json', encoding='utf-8')
        with patch.dict(os.environ, self._env()):
            self.assertEqual(read_kato_settings(), {})

    def test_non_dict_json_degrades_to_empty(self) -> None:
        self.path.write_text('[1, 2, 3]', encoding='utf-8')
        with patch.dict(os.environ, self._env()):
            self.assertEqual(read_kato_settings(), {})

    def test_atomic_write_no_tmp_left_behind(self) -> None:
        with patch.dict(os.environ, self._env()):
            write_kato_settings({'A': '1'})
        leftover = list(self.path.parent.glob('*.tmp'))
        self.assertEqual(leftover, [])

    def test_load_into_environ_populates_unset_keys(self) -> None:
        self.path.write_text(
            json.dumps({'KATO_FRESH_KEY': 'from-settings'}),
            encoding='utf-8',
        )
        with patch.dict(os.environ, self._env()):
            os.environ.pop('KATO_FRESH_KEY', None)
            added = load_kato_settings_into_environ()
            self.assertEqual(os.environ.get('KATO_FRESH_KEY'), 'from-settings')
        self.assertEqual(added, 1)

    def test_load_into_environ_does_not_override_shell(self) -> None:
        # The load-order contract: a real env var (shell, or already
        # set by an earlier loader) wins. settings.json must NOT
        # clobber it.
        self.path.write_text(
            json.dumps({'KATO_PINNED': 'from-settings'}),
            encoding='utf-8',
        )
        with patch.dict(os.environ, self._env({'KATO_PINNED': 'from-shell'})):
            added = load_kato_settings_into_environ()
            self.assertEqual(os.environ.get('KATO_PINNED'), 'from-shell')
        self.assertEqual(added, 0)


if __name__ == '__main__':
    unittest.main()
