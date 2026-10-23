"""Sign in as a service account: an access token, kept until shortly before it expires."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from urllib.parse import urlparse

from core_lib.client.client_base import ClientBase

from google_docs_core_lib.google_docs_core_lib.data.service_account import ServiceAccountKey
from google_docs_core_lib.google_docs_core_lib.errors import GoogleDocsError
from google_docs_core_lib.google_docs_core_lib.helpers.jwt_utils import signed_assertion

JWT_BEARER_GRANT = 'urn:ietf:params:oauth:grant-type:jwt-bearer'
# Renew this long before the token runs out, so a request never carries a
# token that expires on its way.
_RENEW_EARLY_SECONDS = 60


class ServiceAccountTokenClient(ClientBase):
    def __init__(
        self,
        key: ServiceAccountKey,
        scopes: Sequence[str],
        *,
        timeout: int = 30,
        clock: Callable[[], float] = time.time,
    ) -> None:
        parsed = urlparse(key.token_uri)
        super().__init__(f'{parsed.scheme}://{parsed.netloc}')
        self.set_timeout(timeout)
        self._path = parsed.path or '/token'
        self._key = key
        self._scopes = tuple(scopes)
        self._clock = clock
        self._token = ''
        self._expires_at = 0.0

    @property
    def client_email(self) -> str:
        return self._key.client_email

    def access_token(self) -> str:
        """A valid access token, asking Google only when the kept one is about to expire."""
        if self._token and self._clock() < self._expires_at - _RENEW_EARLY_SECONDS:
            return self._token
        now = self._clock()
        try:
            response = self._post(self._path, data={
                'grant_type': JWT_BEARER_GRANT,
                'assertion': signed_assertion(self._key, self._scopes, now=now),
            })
        except OSError as exc:
            raise GoogleDocsError(f'could not reach Google to sign in: {exc}') from exc
        body = _json(response)
        if response.status_code != 200 or not body.get('access_token'):
            reason = body.get('error_description') or body.get('error') or response.status_code
            raise GoogleDocsError(
                f'Google refused the service account {self._key.client_email}: {reason}',
            )
        self._token = str(body['access_token'])
        self._expires_at = now + float(body.get('expires_in', 3600) or 3600)
        return self._token


def _json(response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
