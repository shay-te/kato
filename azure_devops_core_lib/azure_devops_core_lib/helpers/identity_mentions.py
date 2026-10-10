"""Azure DevOps @-mentions, rewritten into a form a mention filter can read.

Azure stores a mention in a comment's text as the identity's GUID —
``@<6A2C8F1E-0F3B-4C6A-9E1D-3B7C2A9F0D11>`` — not as a name. A filter that
knows only ``@login`` (and the brace form ``@{token}``) sees no mention at
all, so a comment addressed to a teammate looked addressed to no one, and a
comment addressed to the bot could never be told apart from one that wasn't.

Each mention is rewritten to the brace form: ``@{<uniqueName>}`` when the
GUID belongs to an identity this pull request already shows (its author,
reviewers, commenters, or the authenticated user), else ``@{<guid>}`` — still
a mention, of someone unknown.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

# ``@<GUID>``; tolerate braces inside the angle brackets and any case.
_GUID = r'[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}'
_MENTION = re.compile(r'@<\{?(' + _GUID + r')\}?>')


def mentioned_identity_ids(text: object) -> set[str]:
    """The lowercased GUIDs ``text`` @-mentions."""
    return {match.group(1).lower() for match in _MENTION.finditer(str(text or ''))}


def identity_names(identities: Iterable[object]) -> dict[str, str]:
    """``{guid (lowercased): name}`` for identity objects Azure returns.

    The name is ``uniqueName`` (the sign-in name — what a host configures as
    the bot's identity), else the display name. Entries without an id or a
    name are skipped.
    """
    names: dict[str, str] = {}
    for identity in identities:
        if not isinstance(identity, Mapping):
            continue
        guid = str(identity.get('id') or '').strip().lower()
        name = str(identity.get('uniqueName') or identity.get('displayName') or '').strip()
        if guid and name:
            names.setdefault(guid, name)
    return names


def rewrite_identity_mentions(text: object, names: Mapping[str, str]) -> str:
    """``text`` with every ``@<GUID>`` as ``@{name}`` (or ``@{guid}``)."""
    def replace(match: re.Match) -> str:
        guid = match.group(1).lower()
        return '@{' + (names.get(guid) or guid) + '}'

    return _MENTION.sub(replace, str(text or ''))
