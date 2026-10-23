"""The service-account key file: read, checked, and refused with a reason."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from google_docs_core_lib.google_docs_core_lib.data.service_account import (
    DEFAULT_TOKEN_URI,
    ServiceAccountKey,
)
from google_docs_core_lib.google_docs_core_lib.errors import GoogleDocsError
from google_docs_core_lib.google_docs_core_lib.tests.fakes import key_mapping, private_key


class ServiceAccountKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.key = private_key()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / 'key.json'

    def _write(self, data) -> Path:
        self.path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding='utf-8')
        return self.path

    def test_a_downloaded_key_is_read(self) -> None:
        key = ServiceAccountKey.from_file(self._write(key_mapping(self.key)))
        self.assertEqual(key.client_email, 'notes-writer@example-project.iam.gserviceaccount.com')
        self.assertEqual(key.private_key_id, 'abc123')
        self.assertIn('PRIVATE KEY', key.private_key)

    def test_a_missing_token_address_means_google_s(self) -> None:
        data = key_mapping(self.key)
        del data['token_uri']
        self.assertEqual(ServiceAccountKey.from_mapping(data).token_uri, DEFAULT_TOKEN_URI)

    def test_a_missing_file_says_so(self) -> None:
        with self.assertRaisesRegex(GoogleDocsError, 'does not exist'):
            ServiceAccountKey.from_file(self.path)

    def test_a_file_that_is_not_json_says_so(self) -> None:
        with self.assertRaisesRegex(GoogleDocsError, 'cannot be read'):
            ServiceAccountKey.from_file(self._write('{nope'))

    def test_a_key_of_another_kind_is_refused_with_where_to_get_the_right_one(self) -> None:
        with self.assertRaisesRegex(GoogleDocsError, 'Add key'):
            ServiceAccountKey.from_file(self._write({'type': 'authorized_user'}))
        with self.assertRaisesRegex(GoogleDocsError, 'not a service-account key'):
            ServiceAccountKey.from_file(self._write(['a', 'list']))

    def test_a_key_without_its_email_or_private_key_is_refused(self) -> None:
        with self.assertRaisesRegex(GoogleDocsError, 'no client_email'):
            ServiceAccountKey.from_mapping(key_mapping(self.key, client_email=''))
        with self.assertRaisesRegex(GoogleDocsError, 'no client_email'):
            ServiceAccountKey.from_mapping(key_mapping(self.key, private_key='nope'))

    def test_a_token_address_that_is_not_google_https_is_refused(self) -> None:
        for uri in ('http://oauth2.googleapis.com/token', 'https://evil.example/token',
                    'https://googleapis.com.evil.example/token'):
            with self.assertRaisesRegex(GoogleDocsError, 'not a Google https address'):
                ServiceAccountKey.from_mapping(key_mapping(self.key, token_uri=uri))

    def test_the_bare_google_host_is_accepted(self) -> None:
        key = ServiceAccountKey.from_mapping(key_mapping(self.key, token_uri='https://googleapis.com/token'))
        self.assertEqual(key.token_uri, 'https://googleapis.com/token')


if __name__ == '__main__':
    unittest.main()
