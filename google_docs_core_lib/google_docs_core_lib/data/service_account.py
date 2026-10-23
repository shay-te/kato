"""A Google service account's key file — the identity that writes the Doc.

Read from the JSON file Google hands out when a key is created for a service
account. Only the fields signing in needs are kept. The token address comes
from the file too, so it is checked: it must be a Google ``https`` address —
a key file edited to point elsewhere would hand a signed sign-in request to a
stranger.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from google_docs_core_lib.google_docs_core_lib.errors import GoogleDocsError

DEFAULT_TOKEN_URI = 'https://oauth2.googleapis.com/token'
_GOOGLE_HOST_SUFFIX = '.googleapis.com'


@dataclass(frozen=True)
class ServiceAccountKey(object):
    client_email: str
    private_key: str
    private_key_id: str = ''
    token_uri: str = DEFAULT_TOKEN_URI

    @classmethod
    def from_file(cls, path: str | Path) -> 'ServiceAccountKey':
        key_path = Path(path).expanduser()
        try:
            data = json.loads(key_path.read_text(encoding='utf-8'))
        except FileNotFoundError as exc:
            raise GoogleDocsError(f'the service-account key file {key_path} does not exist') from exc
        except (OSError, ValueError) as exc:
            raise GoogleDocsError(f'the service-account key file {key_path} cannot be read: {exc}') from exc
        if not isinstance(data, dict) or data.get('type') != 'service_account':
            raise GoogleDocsError(
                f'{key_path} is not a service-account key (download one from Google Cloud: '
                f'IAM → Service accounts → Keys → Add key → JSON)',
            )
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: dict) -> 'ServiceAccountKey':
        email = str(data.get('client_email', '') or '').strip()
        private_key = str(data.get('private_key', '') or '')
        if not email or 'PRIVATE KEY' not in private_key:
            raise GoogleDocsError('the service-account key has no client_email or private_key')
        token_uri = str(data.get('token_uri', '') or '').strip() or DEFAULT_TOKEN_URI
        parsed = urlparse(token_uri)
        host = (parsed.hostname or '').lower()
        if parsed.scheme != 'https' or not (host == _GOOGLE_HOST_SUFFIX[1:] or host.endswith(_GOOGLE_HOST_SUFFIX)):
            raise GoogleDocsError(f'the key\'s token_uri {token_uri!r} is not a Google https address')
        return cls(
            client_email=email,
            private_key=private_key,
            private_key_id=str(data.get('private_key_id', '') or ''),
            token_uri=token_uri,
        )
