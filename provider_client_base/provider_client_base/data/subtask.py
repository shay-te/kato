"""What a tracker reports about an issue and its comments, provider-neutral.

Used by trackers that can hold work as sub-tasks of an issue (see
``IssueClientBase.supports_subtasks``): one read of several issues' state and
assignee, and an issue's comments with their ids and times — what a host needs
to follow sub-tasks that other people may be working on.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IssueStatus(object):
    issue_id: str
    state: str = ''           # the value of the state field asked for ('' = not set)
    assignee: str = ''        # the assignee's STABLE handle (login / accountId); '' = unassigned
    assignee_name: str = ''   # the assignee's display name
    created_at: str = ''      # ISO-8601, UTC
    parent_id: str = ''       # the readable id of the issue this is a sub-task of ('' = none)
    exists: bool = True       # False: the tracker has no such issue (moved, deleted, no access)


@dataclass(frozen=True)
class IssueComment(object):
    comment_id: str
    author: str = ''          # the author's STABLE handle (login / accountId)
    author_name: str = ''
    body: str = ''            # plain text; a code block keeps its lines
    created_at: str = ''      # ISO-8601, UTC
