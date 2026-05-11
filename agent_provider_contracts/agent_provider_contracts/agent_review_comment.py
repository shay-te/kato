"""DTO for a review comment handed to an agent for fixing.

Mirrors the fields kato's ``ReviewComment`` carries that both
backends actually consume — id (``comment_id``), pull-request id,
author, body, file path, line number, line type. Kept as a plain
frozen dataclass so the contracts package stays standalone.

The optional ``all_comments`` field is the full thread context
(other comments on the same line / file) the backends pass into
the fix prompt so the agent sees the conversation, not just the
single comment it's addressing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentReviewComment(object):
    pull_request_id: str = ''
    comment_id: str = ''
    author: str = ''
    body: str = ''
    file_path: str = ''
    line_number: int | str = ''
    line_type: str = ''
    commit_sha: str = ''
    all_comments: list[Any] = field(default_factory=list)
