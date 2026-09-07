"""Unit tests for ``kato_settings_store_utils``.

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

from kato_core_lib.helpers.kato_settings_store_utils import (
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

    def test_path_defaults_to_home_kato_when_unset(self) -> None:
        # No KATO_SETTINGS_FILE → ~/.kato/settings.json. Home is
        # redirected so the test never reads a real home dir.
        fake_home = Path(self._tmp.name) / 'home'
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('KATO_SETTINGS_FILE', None)
            with patch(
                'kato_core_lib.helpers.kato_settings_store_utils.Path.home',
                return_value=fake_home,
            ):
                self.assertEqual(
                    kato_settings_path(),
                    fake_home / '.kato' / 'settings.json',
                )

    def test_write_empty_updates_is_a_noop(self) -> None:
        # write_kato_settings({}) must not create/touch the file —
        # it just returns the current settings.
        with patch.dict(os.environ, self._env()):
            write_kato_settings({'A': '1'})
            result = write_kato_settings({})
        self.assertEqual(result, {'A': '1'})

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

    def test_load_into_environ_never_exports_a_cleared_key(self) -> None:
        """A cleared field must reach the config layer as UNSET, not as ''.

        ``write_kato_settings`` keeps a cleared key in the file on purpose
        ("kato treats empty as unset via ``${oc.env:KEY,"default"}``") — but
        that is only true for a key ABSENT from the environment. omegaconf
        resolves a set-but-empty var to '' and never reaches the default, so
        exporting '' silently disabled the default of every field the
        operator had cleared in the Settings UI.

        The real failure: a cleared ``YOUTRACK_PROGRESS_STATE_FIELD``
        resolved to '' instead of "State", ``move_task_to_in_progress``
        raised ``missing issue field id for: ''``, and kato never started
        the agent on ANY task.
        """
        self.path.write_text(
            json.dumps({
                'YOUTRACK_PROGRESS_STATE_FIELD': '',
                'KATO_REAL_VALUE': 'kept',
            }),
            encoding='utf-8',
        )
        with patch.dict(os.environ, self._env()):
            os.environ.pop('YOUTRACK_PROGRESS_STATE_FIELD', None)
            os.environ.pop('KATO_REAL_VALUE', None)
            added = load_kato_settings_into_environ()
            self.assertNotIn('YOUTRACK_PROGRESS_STATE_FIELD', os.environ)
            self.assertEqual(os.environ.get('KATO_REAL_VALUE'), 'kept')
        self.assertEqual(added, 1)

    def test_a_cleared_key_does_not_unset_a_real_shell_value(self) -> None:
        # Skipping the export must not become "delete what the shell set".
        self.path.write_text(json.dumps({'KATO_PINNED': ''}), encoding='utf-8')
        with patch.dict(os.environ, self._env({'KATO_PINNED': 'from-shell'})):
            load_kato_settings_into_environ()
            self.assertEqual(os.environ.get('KATO_PINNED'), 'from-shell')


if __name__ == '__main__':
    unittest.main()
