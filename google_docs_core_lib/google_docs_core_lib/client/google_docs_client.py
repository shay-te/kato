"""Replace a Google Doc's content.

Uses Google Drive's import: the new content is uploaded as HTML into the
EXISTING document (``files.update``, media upload), and Drive converts it to
the Doc's own headings, lists and links. The document keeps its id, link and
sharing; only its body is replaced — anything edited by hand in the Doc is
overwritten by design (the caller's own copy is the master).

The service account must have been given edit access to the Doc (share it
with the account's email, as an editor), and the Drive API must be enabled
in the account's Google Cloud project. Both failures are reported as what
to do, not as a status code.
"""

from __future__ import annotations

from core_lib.client.client_base import ClientBase

from google_docs_core_lib.google_docs_core_lib.client.service_account_token_client import (
    ServiceAccountTokenClient,
)
from google_docs_core_lib.google_docs_core_lib.data.service_account import ServiceAccountKey
from google_docs_core_lib.google_docs_core_lib.errors import GoogleDocsError

DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive'
GOOGLE_API_BASE_URL = 'https://www.googleapis.com'


class GoogleDocsClient(ClientBase):
    def __init__(
        self,
        tokens: ServiceAccountTokenClient,
        *,
        base_url: str = GOOGLE_API_BASE_URL,
        timeout: int = 60,
    ) -> None:
        super().__init__(base_url)
        self.set_timeout(timeout)
        self._tokens = tokens

    @classmethod
    def from_key_file(cls, path, **kwargs) -> 'GoogleDocsClient':
        key = ServiceAccountKey.from_file(path)
        return cls(ServiceAccountTokenClient(key, [DRIVE_SCOPE]), **kwargs)

    @property
    def client_email(self) -> str:
        return self._tokens.client_email

    def replace_content(self, document_id: str, html: str) -> None:
        """Make ``html`` the whole content of the Doc ``document_id``."""
        document = str(document_id or '').strip()
        if not document:
            raise GoogleDocsError('no Google Doc id was given')
        headers = {
            'Authorization': f'Bearer {self._tokens.access_token()}',
            'Content-Type': 'text/html; charset=utf-8',
        }
        try:
            response = self._patch(
                f'upload/drive/v3/files/{document}',
                params={'uploadType': 'media', 'supportsAllDrives': 'true'},
                data=html.encode('utf-8'),
                headers=headers,
            )
        except OSError as exc:
            raise GoogleDocsError(f'could not reach Google Drive: {exc}') from exc
        if response.status_code in (200, 201):
            return
        raise GoogleDocsError(self._explain(response, document))

    def _patch(self, path: str, **kwargs):
        # ``ClientBase`` has no PATCH; build it the way its other verbs are built.
        return self.session.patch(
            f'{self.base_url.rstrip("/")}/{path.lstrip("/")}', **self.process_kwargs(**kwargs),
        )

    def _explain(self, response, document: str) -> str:
        reason = _google_reason(response)
        account = self.client_email
        if response.status_code == 404:
            return (
                f'no Google Doc {document} that {account} can see — check the link, and share '
                f'the Doc with {account} as an editor'
            )
        if response.status_code == 403:
            if 'accessNotConfigured' in reason or 'has not been used' in reason or 'disabled' in reason:
                return f'the Google Drive API is not enabled for {account}\'s project: {reason}'
            return f'{account} may not edit Doc {document} — share it with {account} as an editor ({reason})'
        if response.status_code == 401:
            return f'Google did not accept {account}\'s sign-in: {reason}'
        return f'Google Drive answered {response.status_code} for Doc {document}: {reason}'


def _google_reason(response) -> str:
    try:
        body = response.json()
    except ValueError:
        return (getattr(response, 'text', '') or '').strip()[:300]
    error = body.get('error') if isinstance(body, dict) else None
    if isinstance(error, dict):
        details = [str(item.get('reason', '')) for item in error.get('errors', []) if isinstance(item, dict)]
        message = str(error.get('message', '') or '')
        return '; '.join(part for part in [message, *details] if part)
    return str(error or '')[:300]
