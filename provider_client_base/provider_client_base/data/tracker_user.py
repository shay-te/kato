"""A person an issue can be assigned to, provider-neutral.

Used by trackers that can hand an issue over to someone else (see
``IssueClientBase.supports_handover``): the picker lists them, and assigning
uses ``handle`` — the STABLE identity the tracker assigns by (a YouTrack login,
a Jira accountId), never the display name.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrackerUser(object):
    handle: str
    name: str = ''
