"""The signed sign-in request: right claims, a signature the public key verifies."""

from __future__ import annotations

import base64
import json
import unittest

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from google_docs_core_lib.google_docs_core_lib.data.service_account import ServiceAccountKey
from google_docs_core_lib.google_docs_core_lib.errors import GoogleDocsError
from google_docs_core_lib.google_docs_core_lib.helpers.jwt_utils import (
    ASSERTION_LIFETIME_SECONDS,
    signed_assertion,
)
from google_docs_core_lib.google_docs_core_lib.tests.fakes import key_mapping, private_key


def _decode(part: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))


class SignedAssertionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rsa = private_key()
        cls.key = ServiceAccountKey.from_mapping(key_mapping(cls.rsa))

    def test_the_claims_name_the_account_scopes_and_audience(self) -> None:
        header, claims, _ = signed_assertion(self.key, ['s1', 's2'], now=1_000.7).split('.')
        self.assertEqual(_decode(header), {'alg': 'RS256', 'typ': 'JWT', 'kid': 'abc123'})
        self.assertEqual(_decode(claims), {
            'iss': self.key.client_email, 'scope': 's1 s2', 'aud': self.key.token_uri,
            'iat': 1000, 'exp': 1000 + ASSERTION_LIFETIME_SECONDS,
        })

    def test_the_signature_verifies_with_the_public_key(self) -> None:
        header, claims, signature = signed_assertion(self.key, ['s'], now=0).split('.')
        raw = base64.urlsafe_b64decode(signature + '=' * (-len(signature) % 4))
        self.rsa.public_key().verify(
            raw, f'{header}.{claims}'.encode('ascii'), padding.PKCS1v15(), hashes.SHA256(),
        )  # raises if wrong

    def test_no_key_id_means_no_kid(self) -> None:
        key = ServiceAccountKey.from_mapping(key_mapping(self.rsa, private_key_id=''))
        self.assertNotIn('kid', _decode(signed_assertion(key, ['s'], now=0).split('.')[0]))

    def test_an_unreadable_private_key_is_reported(self) -> None:
        broken = ServiceAccountKey(client_email='a@b', private_key='-----BEGIN PRIVATE KEY-----\nnope\n')
        with self.assertRaisesRegex(GoogleDocsError, 'private key cannot be read'):
            signed_assertion(broken, ['s'], now=0)


if __name__ == '__main__':
    unittest.main()
