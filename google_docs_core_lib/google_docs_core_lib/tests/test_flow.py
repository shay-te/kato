"""End to end: a key file on disk → sign in → the Doc's content replaced.

Only the HTTP session is faked; the key is read from a real file and the
sign-in request is really signed.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from google_docs_core_lib.google_docs_core_lib.client.google_docs_client import GoogleDocsClient
from google_docs_core_lib.google_docs_core_lib.tests.fakes import (
    FakeResponse,
    FakeSession,
    key_mapping,
    private_key,
)


class GoogleDocsFlowTests(unittest.TestCase):
    def test_a_key_file_signs_in_once_and_replaces_the_doc_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / 'key.json'
            key_file.write_text(json.dumps(key_mapping(private_key())), encoding='utf-8')

            client = GoogleDocsClient.from_key_file(key_file)
            tokens = client._tokens
            tokens.session = FakeSession(post=[FakeResponse(200, {'access_token': 'tok', 'expires_in': 3600})])
            client.session = FakeSession(patch=[FakeResponse(200, {}), FakeResponse(200, {})])

            client.replace_content('DOC', '<p>first</p>')
            client.replace_content('DOC', '<p>second</p>')

        self.assertEqual(client.client_email, 'notes-writer@example-project.iam.gserviceaccount.com')
        self.assertEqual(len(tokens.session.calls), 1)  # one sign-in for both writes
        self.assertEqual(
            [call[2]['data'] for call in client.session.calls], [b'<p>first</p>', b'<p>second</p>'],
        )
        self.assertTrue(all(call[2]['headers']['Authorization'] == 'Bearer tok'
                            for call in client.session.calls))


if __name__ == '__main__':
    unittest.main()
