"""Normalise Jira timestamps to ISO-8601 UTC."""
from __future__ import annotations

from datetime import datetime, timezone


def iso_utc(value: object) -> str:
    """A Jira timestamp as ISO-8601 in UTC; '' when blank or unreadable.

    Jira writes ``2026-10-17T13:00:00.000+0300`` — milliseconds, an offset
    without a colon, and the offset of the account's time zone rather than
    UTC. Converted here to ``2026-10-17T10:00:00+00:00`` so times read from
    different accounts compare as plain strings. A time with no offset is
    taken as UTC.
    """
    text = str(value or '').strip()
    if not text:
        return ''
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return ''
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()
