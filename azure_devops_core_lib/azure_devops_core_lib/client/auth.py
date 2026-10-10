from __future__ import annotations

from base64 import b64encode


def pat_basic_auth_header(token: str) -> str:
    """``Authorization`` for an Azure DevOps personal access token.

    A PAT is sent as HTTP Basic with an EMPTY user name — ``base64(":" + PAT)``.
    The shared client base sends ``Bearer``, which Azure DevOps accepts only
    for Entra ID / OAuth tokens, so a PAT sent that way is refused with 401.
    """
    encoded = b64encode(f':{token or ""}'.encode('utf-8')).decode('ascii')
    return f'Basic {encoded}'
