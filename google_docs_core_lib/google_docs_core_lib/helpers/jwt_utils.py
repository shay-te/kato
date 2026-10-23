"""The signed sign-in request a service account exchanges for an access token.

Google's "JWT bearer" grant: a JSON Web Token naming the account, the scopes
and Google's token address, signed RS256 with the account's private key.
Built with ``cryptography`` directly — one signature does not need a whole
auth library.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from google_docs_core_lib.google_docs_core_lib.data.service_account import ServiceAccountKey
from google_docs_core_lib.google_docs_core_lib.errors import GoogleDocsError

# A token request older than this is refused by Google anyway (max one hour).
ASSERTION_LIFETIME_SECONDS = 3600


def signed_assertion(key: ServiceAccountKey, scopes: Sequence[str], *, now: float) -> str:
    header = {'alg': 'RS256', 'typ': 'JWT'}
    if key.private_key_id:
        header['kid'] = key.private_key_id
    issued = int(now)
    claims = {
        'iss': key.client_email,
        'scope': ' '.join(scopes),
        'aud': key.token_uri,
        'iat': issued,
        'exp': issued + ASSERTION_LIFETIME_SECONDS,
    }
    signing_input = f'{_b64_json(header)}.{_b64_json(claims)}'.encode('ascii')
    return f'{signing_input.decode("ascii")}.{_b64(_sign(key.private_key, signing_input))}'


def _sign(private_key_pem: str, data: bytes) -> bytes:
    try:
        private_key = serialization.load_pem_private_key(private_key_pem.encode('utf-8'), password=None)
    except (ValueError, TypeError) as exc:
        raise GoogleDocsError(f'the service-account private key cannot be read: {exc}') from exc
    return private_key.sign(data, padding.PKCS1v15(), hashes.SHA256())


def _b64_json(value: dict) -> str:
    return _b64(json.dumps(value, separators=(',', ':')).encode('utf-8'))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')
