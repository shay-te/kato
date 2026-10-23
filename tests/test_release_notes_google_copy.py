"""The Google Doc copy: opt-in, one upload per burst, failures shown, file untouched.

Real settings file, real release-notes file, the scheduler run by hand; the
Google client is the stand-in (its own lib is tested against HTTP in
google_docs_core_lib).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kato_core_lib.data_layers.service.release_notes_google_sync import ReleaseNotesGoogleSync
from kato_core_lib.helpers.release_notes_html_utils import release_notes_html

DOC = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'


class ReleaseNotesHtmlTests(unittest.TestCase):
    def test_the_document_shapes_become_html_and_markers_vanish(self) -> None:
        out = release_notes_html(
            '# Release notes\n\n## Unreleased\n\n### backend\n\n'
            '- 2026-10-21 — Clients can set a password (UNA-1) · [PR](https://git.example/pr/7) '
            '<!-- kato:task UNA-1 -->\n\nA note.\n',
        )
        self.assertIn('<h1>Release notes</h1>', out)
        self.assertIn('<h3>backend</h3>', out)
        self.assertIn('<ul>\n<li>2026-10-21 — Clients can set a password (UNA-1) · '
                      '<a href="https://git.example/pr/7">PR</a></li>\n</ul>', out)
        self.assertIn('<p>A note.</p>', out)
        self.assertNotIn('kato:task', out)

    def test_text_can_never_become_markup(self) -> None:
        out = release_notes_html('- <script>alert(1)</script> & [x](javascript:alert(1))\n')
        self.assertIn('&lt;script&gt;', out)
        self.assertNotIn('<script>', out)
        self.assertNotIn('href="javascript', out)

    def test_code_fences_are_kept_as_text(self) -> None:
        out = release_notes_html('- item\n```\n## not a heading\n<b>\n```\n')
        self.assertIn('</ul>\n<pre>\n## not a heading\n&lt;b&gt;\n</pre>', out)

    def test_an_unclosed_list_or_fence_is_closed(self) -> None:
        self.assertTrue(release_notes_html('- a').rstrip().endswith('</ul>\n</body></html>'))
        self.assertIn('</pre>', release_notes_html('```\ncode'))


class ReleaseNotesGoogleSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.settings_file = root / 'settings.json'
        self.notes = root / 'notes.md'
        self.notes.write_text('# Release notes\n\n- a line\n', encoding='utf-8')
        self.key = root / 'key.json'
        self.key.write_text('{}', encoding='utf-8')
        env = patch.dict(os.environ, {'KATO_SETTINGS_FILE': str(self.settings_file)})
        env.start()
        self.addCleanup(env.stop)
        self.client = MagicMock()
        self.scheduled: list = []
        self.sync = ReleaseNotesGoogleSync(
            client_factory=lambda credentials: self.client,
            schedule=lambda seconds, work: self.scheduled.append(work),
            status_path=root / 'sync.json', clock=lambda: 1_700_000_000.0, logger=MagicMock(),
        )
        self._settings(destination='file_and_google_doc')

    def _settings(self, *, destination, doc=DOC, key=None) -> None:
        self.settings_file.write_text(json.dumps({
            'KATO_RELEASE_NOTES_PATH': str(self.notes),
            'KATO_RELEASE_NOTES_DESTINATION': destination,
            'KATO_RELEASE_NOTES_GOOGLE_DOC_URL': f'https://docs.google.com/document/d/{doc}/edit' if doc else '',
            'KATO_RELEASE_NOTES_GOOGLE_CREDENTIALS_PATH': str(key or self.key),
        }), encoding='utf-8')

    def test_the_file_only_destination_never_calls_google(self) -> None:
        self._settings(destination='file')
        self.sync.request()
        self.assertEqual(self.scheduled, [])
        self.assertFalse(self.sync.sync_now()['wanted'])
        self.client.replace_content.assert_not_called()

    def test_a_burst_of_changes_is_one_upload(self) -> None:
        self.sync.request()
        self.sync.request()
        self.sync.request()
        self.assertEqual(len(self.scheduled), 1)
        self.scheduled[0]()
        self.client.replace_content.assert_called_once()
        document, html = self.client.replace_content.call_args.args
        self.assertEqual(document, DOC)
        self.assertIn('<li>a line</li>', html)
        # After the upload, the next change waits for its own.
        self.sync.request()
        self.assertEqual(len(self.scheduled), 2)

    def test_a_good_copy_is_recorded(self) -> None:
        status = self.sync.sync_now()
        self.assertEqual((status['error'], status['last_synced_at'], status['document_id']),
                         ('', 1_700_000_000.0, DOC))

    def test_a_failure_is_shown_and_the_local_file_is_untouched(self) -> None:
        self.client.replace_content.side_effect = RuntimeError('share the Doc with notes@ as an editor')
        before = self.notes.read_text(encoding='utf-8')
        status = self.sync.sync_now()
        self.assertIn('share the Doc', status['error'])
        self.assertEqual(self.notes.read_text(encoding='utf-8'), before)
        # Sync now, after fixing it, clears the error.
        self.client.replace_content.side_effect = None
        self.assertEqual(self.sync.sync_now()['error'], '')

    def test_missing_settings_say_what_to_set(self) -> None:
        self._settings(destination='file_and_google_doc', doc='')
        self.assertIn('Google Doc link', self.sync.sync_now()['error'])
        self.client.replace_content.assert_not_called()

    def test_a_bad_key_file_is_a_shown_failure_not_a_crash(self) -> None:
        def no_client(credentials):
            raise ValueError('not a service-account key')

        self.sync._client_factory = no_client
        self.assertIn('service-account', self.sync.sync_now()['error'])

    def test_a_status_file_that_cannot_be_read_or_written_is_survived(self) -> None:
        self.sync._status_path.write_text('{nope', encoding='utf-8')
        self.assertEqual(self.sync.status()['last_synced_at'], 0.0)
        self.sync._status_path.unlink()
        self.sync._status_path.mkdir()  # a folder where the file should be
        self.assertEqual(self.sync.sync_now()['error'], '')
        self.sync.logger.warning.assert_called()


if __name__ == '__main__':
    unittest.main()
