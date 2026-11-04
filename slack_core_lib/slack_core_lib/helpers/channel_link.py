"""Which channel a person means, from what they pasted.

Slack's "Copy link" on a channel gives ``https://<team>.slack.com/archives/<id>``
(a message link adds ``/p<timestamp>``); the browser's address bar shows
``https://app.slack.com/client/<team id>/<id>``. Both carry the channel id, and
so does the id on its own. ``#name`` is passed on as a name, which Slack accepts
for a channel the app is in.

An Incoming Webhook URL (``https://hooks.slack.com/services/T…/B…/…``) is a
channel of its own: whatever is posted to it lands in the channel it was made
for, with no app token at all.
"""

from __future__ import annotations

import re

# Public (C), private (G) and direct (D) conversations.
_CHANNEL_ID = r'[CGD][A-Z0-9]{8,}'
_IN_LINK = re.compile(rf'/(?:archives|client/T[A-Z0-9]+)/({_CHANNEL_ID})(?:[/?#]|$)')
_BARE_ID = re.compile(rf'^{_CHANNEL_ID}$')
_NAME = re.compile(r'^#([a-z0-9][a-z0-9._-]{0,79})$')


def channel_from_link(text: object) -> str:
    """The channel id (or ``#name``) in ``text``; '' when there is none."""
    value = str(text or '').strip()
    if not value:
        return ''
    found = _IN_LINK.search(value)
    if found:
        return found.group(1)
    if _BARE_ID.match(value):
        return value
    named = _NAME.match(value.lower())
    return f'#{named.group(1)}' if named else ''


_WEBHOOK = re.compile(r'^https://hooks\.slack(?:-gov)?\.com/services/[A-Za-z0-9]+/[A-Za-z0-9]+/[A-Za-z0-9]+$')


def webhook_url(text: object) -> str:
    """``text`` when it is a Slack Incoming Webhook URL ('' when it is not)."""
    value = str(text or '').strip().rstrip('/')
    return value if _WEBHOOK.match(value) else ''
