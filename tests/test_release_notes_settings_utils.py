"""Release-notes settings: read fresh, so a change applies without a restart.

``settings.json`` (what the Settings drawer writes) wins over the shell, which
wins over the default — and a path inside the workspaces folder is refused,
because a file there would sit in a task's clone and be committed with it.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.helpers import release_notes_settings_utils as settings


class ReleaseNotesSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.settings_file = self.root / 'settings.json'
        env = patch.dict(os.environ, {'KATO_SETTINGS_FILE': str(self.settings_file)}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for key in settings.RELEASE_NOTES_KEYS | {'KATO_WORKSPACES_ROOT'}:
            os.environ.pop(key, None)

    def _save(self, **values) -> None:
        self.settings_file.write_text(json.dumps(values), encoding='utf-8')

    def test_defaults_are_on_the_file_in_kato_home(self) -> None:
        self.assertTrue(settings.release_notes_enabled({}))
        self.assertEqual(settings.release_notes_path({}), Path.home() / '.kato' / 'release-notes.md')
        self.assertEqual(settings.release_notes_destination({}), 'file')
        self.assertEqual(settings.google_doc_id({}), '')
        self.assertIsNone(settings.google_credentials_path({}))

    def test_settings_json_wins_over_the_shell_and_is_read_fresh(self) -> None:
        env = {settings.RELEASE_NOTES_PATH_KEY: '/from/shell.md'}
        self.assertEqual(settings.release_notes_path(env), Path('/from/shell.md'))
        self._save(KATO_RELEASE_NOTES_PATH='~/notes/rn.md')
        # No restart: the very next read sees the new value.
        self.assertEqual(settings.release_notes_path(env), Path.home() / 'notes' / 'rn.md')
        self._save(KATO_RELEASE_NOTES_ENABLED='false')
        self.assertFalse(settings.release_notes_enabled({}))

    def test_an_unknown_destination_means_the_file_only(self) -> None:
        self._save(KATO_RELEASE_NOTES_DESTINATION='carrier-pigeon')
        self.assertEqual(settings.release_notes_destination({}), 'file')
        self._save(KATO_RELEASE_NOTES_DESTINATION='FILE_AND_GOOGLE_DOC')
        self.assertEqual(settings.release_notes_destination({}), 'file_and_google_doc')

    def test_a_corrupt_settings_file_falls_back_to_the_shell(self) -> None:
        self.settings_file.write_text('{nope', encoding='utf-8')
        self.assertFalse(settings.release_notes_enabled({settings.RELEASE_NOTES_ENABLED_KEY: 'off'}))

    def test_the_doc_id_is_read_from_a_link_or_taken_bare(self) -> None:
        doc = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'
        self.assertEqual(settings.google_doc_id_from(f'https://docs.google.com/document/d/{doc}/edit'), doc)
        self.assertEqual(settings.google_doc_id_from(f'https://docs.google.com/document/u/1/d/{doc}/'), doc)
        self.assertEqual(settings.google_doc_id_from(doc), doc)
        self.assertEqual(settings.google_doc_id_from('https://example.com/nope'), '')

    def test_a_path_inside_the_workspaces_folder_is_refused(self) -> None:
        workspaces = self.root / 'workspaces'
        (workspaces / 'UNA-1').mkdir(parents=True)
        problem = settings.path_problem(workspaces / 'UNA-1' / 'notes.md', workspaces=workspaces)
        self.assertIn('inside the workspaces folder', problem)
        self.assertEqual(settings.path_problem(self.root / 'notes.md', workspaces=workspaces), '')

    def test_a_folder_is_not_a_file(self) -> None:
        self.assertIn('is a folder', settings.path_problem(self.root, workspaces=self.root / 'w'))

    def test_settings_errors_check_the_path_against_the_root_being_saved_too(self) -> None:
        workspaces = self.root / 'ws'
        errors = settings.settings_errors({
            'KATO_RELEASE_NOTES_PATH': str(workspaces / 'x.md'),
            'KATO_WORKSPACES_ROOT': str(workspaces),
        })
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith('KATO_RELEASE_NOTES_PATH:'))
        self.assertEqual(settings.settings_errors({'KATO_RELEASE_NOTES_PATH': ''}), [])

    def test_a_doc_link_that_is_not_a_google_doc_is_refused(self) -> None:
        errors = settings.settings_errors({'KATO_RELEASE_NOTES_GOOGLE_DOC_URL': 'https://example.com'})
        self.assertEqual(len(errors), 1)
        self.assertIn('Google Doc', errors[0])

    def test_the_workspaces_root_follows_settings_then_default(self) -> None:
        self.assertEqual(settings.workspaces_root({}), Path.home() / '.kato' / 'workspaces')
        self._save(KATO_WORKSPACES_ROOT='/somewhere/ws')
        self.assertEqual(settings.workspaces_root({}), Path('/somewhere/ws'))


if __name__ == '__main__':
    unittest.main()
