"""Signing in and replacing the Doc — every answer Google can give, as a sentence."""

from __future__ import annotations

import unittest

from core_lib.client.client_base import ClientBase

from google_docs_core_lib.google_docs_core_lib.client.google_docs_client import (
    DRIVE_SCOPE,
    GoogleDocsClient,
)
from google_docs_core_lib.google_docs_core_lib.client.service_account_token_client import (
    JWT_BEARER_GRANT,
    ServiceAccountTokenClient,
)
from google_docs_core_lib.google_docs_core_lib.data.service_account import ServiceAccountKey
from google_docs_core_lib.google_docs_core_lib.errors import GoogleDocsError
from google_docs_core_lib.google_docs_core_lib.tests.fakes import (
    FakeResponse,
    FakeSession,
    key_mapping,
    private_key,
)

TOKEN_OK = FakeResponse(200, {'access_token': 'tok-1', 'expires_in': 3600})


class _Clock(object):
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class ServiceAccountTokenClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.key = ServiceAccountKey.from_mapping(key_mapping(private_key()))

    def setUp(self) -> None:
        self.clock = _Clock()
        self.tokens = ServiceAccountTokenClient(self.key, ['scope-a'], clock=self.clock)

    def _answer(self, *responses) -> FakeSession:
        self.tokens.session = FakeSession(post=responses)
        return self.tokens.session

    def test_it_is_a_core_lib_client(self) -> None:
        self.assertIsInstance(self.tokens, ClientBase)
        self.assertEqual(self.tokens.base_url, 'https://oauth2.googleapis.com')

    def test_a_token_is_asked_for_with_a_signed_jwt_bearer_request(self) -> None:
        session = self._answer(TOKEN_OK)
        self.assertEqual(self.tokens.access_token(), 'tok-1')
        verb, url, kwargs = session.calls[0]
        self.assertEqual((verb, url), ('POST', 'https://oauth2.googleapis.com/token'))
        self.assertEqual(kwargs['data']['grant_type'], JWT_BEARER_GRANT)
        self.assertEqual(kwargs['data']['assertion'].count('.'), 2)
        self.assertEqual(kwargs['timeout'], 30)

    def test_the_token_is_kept_until_shortly_before_it_expires(self) -> None:
        session = self._answer(TOKEN_OK, FakeResponse(200, {'access_token': 'tok-2', 'expires_in': 3600}))
        self.tokens.access_token()
        self.clock.now += 3000
        self.assertEqual(self.tokens.access_token(), 'tok-1')
        self.clock.now += 560  # within the last minute
        self.assertEqual(self.tokens.access_token(), 'tok-2')
        self.assertEqual(len(session.calls), 2)

    def test_a_refusal_names_the_account_and_google_s_reason(self) -> None:
        self._answer(FakeResponse(400, {'error': 'invalid_grant', 'error_description': 'Invalid JWT'}))
        with self.assertRaisesRegex(GoogleDocsError, 'refused the service account .*Invalid JWT'):
            self.tokens.access_token()

    def test_a_reply_without_a_token_or_json_is_a_refusal(self) -> None:
        self._answer(FakeResponse(200, {'token_type': 'Bearer'}))
        with self.assertRaisesRegex(GoogleDocsError, 'refused'):
            self.tokens.access_token()
        self._answer(FakeResponse(502, None, text='bad gateway'))
        with self.assertRaisesRegex(GoogleDocsError, '502'):
            self.tokens.access_token()
        self._answer(FakeResponse(200, ['not', 'a', 'dict']))
        with self.assertRaisesRegex(GoogleDocsError, 'refused'):
            self.tokens.access_token()

    def test_a_missing_expiry_means_an_hour(self) -> None:
        self._answer(FakeResponse(200, {'access_token': 'tok', 'expires_in': None}), TOKEN_OK)
        self.tokens.access_token()
        self.clock.now += 3000
        self.assertEqual(self.tokens.access_token(), 'tok')

    def test_no_network_says_so(self) -> None:
        self._answer(ConnectionError('offline'))
        with self.assertRaisesRegex(GoogleDocsError, 'could not reach Google'):
            self.tokens.access_token()

    def test_a_token_address_without_a_path_posts_to_token(self) -> None:
        key = ServiceAccountKey(client_email='a@b', private_key=self.key.private_key,
                                token_uri='https://oauth2.googleapis.com')
        tokens = ServiceAccountTokenClient(key, ['s'], clock=self.clock)
        tokens.session = FakeSession(post=[TOKEN_OK])
        tokens.access_token()
        self.assertEqual(tokens.session.calls[0][1], 'https://oauth2.googleapis.com/token')


class _Tokens(object):
    client_email = 'notes-writer@example-project.iam.gserviceaccount.com'

    def access_token(self) -> str:
        return 'tok-1'


class GoogleDocsClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = GoogleDocsClient(_Tokens())

    def _answer(self, *responses) -> FakeSession:
        self.client.session = FakeSession(patch=responses)
        return self.client.session

    def test_it_is_a_core_lib_client(self) -> None:
        self.assertIsInstance(self.client, ClientBase)

    def test_the_doc_is_replaced_with_an_html_media_upload(self) -> None:
        session = self._answer(FakeResponse(200, {'id': 'DOC'}))
        self.client.replace_content('DOC', '<h1>Notes</h1>')
        verb, url, kwargs = session.calls[0]
        self.assertEqual((verb, url), ('PATCH', 'https://www.googleapis.com/upload/drive/v3/files/DOC'))
        self.assertEqual(kwargs['params'], {'uploadType': 'media', 'supportsAllDrives': 'true'})
        self.assertEqual(kwargs['data'], '<h1>Notes</h1>'.encode('utf-8'))
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer tok-1')
        self.assertTrue(kwargs['headers']['Content-Type'].startswith('text/html'))
        self.assertEqual(kwargs['timeout'], 60)

    def test_no_doc_id_is_refused_before_any_call(self) -> None:
        session = self._answer()
        with self.assertRaisesRegex(GoogleDocsError, 'no Google Doc id'):
            self.client.replace_content('  ', 'x')
        self.assertEqual(session.calls, [])

    def test_a_doc_it_cannot_see_says_to_share_it(self) -> None:
        self._answer(FakeResponse(404, {'error': {'message': 'File not found: DOC.'}}))
        with self.assertRaisesRegex(GoogleDocsError, 'share the Doc with notes-writer@'):
            self.client.replace_content('DOC', 'x')

    def test_a_doc_it_may_not_edit_says_to_share_it_as_an_editor(self) -> None:
        self._answer(FakeResponse(403, {'error': {
            'message': 'The user does not have sufficient permissions for this file.',
            'errors': [{'reason': 'insufficientFilePermissions'}],
        }}))
        with self.assertRaisesRegex(GoogleDocsError, 'as an editor .*insufficientFilePermissions'):
            self.client.replace_content('DOC', 'x')

    def test_a_disabled_drive_api_says_to_enable_it(self) -> None:
        self._answer(FakeResponse(403, {'error': {
            'message': 'Google Drive API has not been used in project 123 before or it is disabled.',
            'errors': [{'reason': 'accessNotConfigured'}],
        }}))
        with self.assertRaisesRegex(GoogleDocsError, 'Drive API is not enabled'):
            self.client.replace_content('DOC', 'x')

    def test_other_answers_carry_the_status_and_reason(self) -> None:
        self._answer(FakeResponse(401, {'error': {'message': 'Invalid Credentials'}}))
        with self.assertRaisesRegex(GoogleDocsError, 'did not accept .*Invalid Credentials'):
            self.client.replace_content('DOC', 'x')
        self._answer(FakeResponse(500, None, text='backend error'))
        with self.assertRaisesRegex(GoogleDocsError, '500 for Doc DOC: backend error'):
            self.client.replace_content('DOC', 'x')
        self._answer(FakeResponse(429, {'error': 'rateLimitExceeded'}))
        with self.assertRaisesRegex(GoogleDocsError, '429 .*rateLimitExceeded'):
            self.client.replace_content('DOC', 'x')

    def test_no_network_says_so(self) -> None:
        self._answer(ConnectionError('offline'))
        with self.assertRaisesRegex(GoogleDocsError, 'could not reach Google Drive'):
            self.client.replace_content('DOC', 'x')

    def test_the_drive_scope_is_full_drive(self) -> None:
        # A Doc shared with the account is not one it created, so drive.file is not enough.
        self.assertEqual(DRIVE_SCOPE, 'https://www.googleapis.com/auth/drive')


if __name__ == '__main__':
    unittest.main()
