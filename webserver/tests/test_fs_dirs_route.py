"""Coverage for ``/api/fs/dirs`` — the folder-picker listing behind the
"Browse…" button (wizard step 3 + Settings → Repositories).

Real filesystem trees in a tmpdir; no mocks of the listing logic. The
contract: DIRECTORY names only (never files), hidden dirs skipped, parent
navigation, and clean errors for missing/invalid paths.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kato_webserver.app import create_app


class _FakeManager:
    def list_records(self):
        return []

    def get_record(self, task_id):  # noqa: ARG002
        return None

    def get_session(self, task_id):  # noqa: ARG002
        return None


class FsDirsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # resolve(): the route resolves symlinks (macOS /var → /private/var),
        # so the expected paths must be resolved the same way.
        self.root = Path(self._tmp.name).resolve()
        (self.root / 'projects').mkdir()
        (self.root / 'projects' / 'repo-a').mkdir()
        (self.root / 'projects' / 'repo-b').mkdir()
        (self.root / 'projects' / '.hidden').mkdir()
        (self.root / 'projects' / 'notes.txt').write_text('x', encoding='utf-8')
        self.client = create_app(session_manager=_FakeManager()).test_client()

    def _get(self, path):
        return self.client.get('/api/fs/dirs', query_string={'path': path})

    def test_lists_directories_only_sorted(self) -> None:
        response = self._get(str(self.root / 'projects'))
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(
            [d['name'] for d in body['dirs']], ['repo-a', 'repo-b'],
        )
        # Files and hidden directories never appear.
        names = {d['name'] for d in body['dirs']}
        self.assertNotIn('notes.txt', names)
        self.assertNotIn('.hidden', names)
        # Each entry carries the absolute path the picker will submit.
        self.assertEqual(
            body['dirs'][0]['path'], str(self.root / 'projects' / 'repo-a'),
        )

    def test_reports_parent_for_up_navigation(self) -> None:
        body = self._get(str(self.root / 'projects')).get_json()
        self.assertEqual(body['parent'], str(self.root))
        self.assertEqual(body['path'], str(self.root / 'projects'))

    def test_home_shortcut_and_tilde_default(self) -> None:
        response = self.client.get('/api/fs/dirs')  # no path → ~
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['path'], str(Path.home()))
        self.assertEqual(body['home'], str(Path.home()))

    def test_missing_directory_is_404(self) -> None:
        response = self._get(str(self.root / 'ghost'))
        self.assertEqual(response.status_code, 404)
        self.assertIn('not a directory', response.get_json()['error'])

    def test_a_file_path_is_404(self) -> None:
        response = self._get(str(self.root / 'projects' / 'notes.txt'))
        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
