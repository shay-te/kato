from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PullRequest(object):
    id: str = ''
    title: str = ''
    url: str = ''
    # 'open' | 'merged' | 'declined' (closed without merging); '' when the
    # provider's state is unknown, so it is never mistaken for merged.
    state: str = ''
    destination_branch: str = ''
    # The provider's own creation timestamp, as it sent it.
    created_at: str = ''
