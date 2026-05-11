from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewComment(object):
    pull_request_id: str = ''
    comment_id: str = ''
    author: str = ''
    body: str = ''
    resolution_target_id: str = ''
    resolution_target_type: str = ''
    resolvable: bool = False
