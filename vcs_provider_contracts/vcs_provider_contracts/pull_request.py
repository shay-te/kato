from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PullRequest(object):
    id: str = ''
    title: str = ''
    url: str = ''
