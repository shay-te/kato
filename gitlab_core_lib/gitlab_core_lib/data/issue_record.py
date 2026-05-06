from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IssueRecord:
    """Neutral data transfer object for one GitLab issue.

    Returned by ``GitLabIssuesClient.get_assigned_tasks``.
    Field names mirror the Task interface so duck-typed
    orchestrators can use it without an explicit conversion step.
    """

    id: str = ''
    summary: str = ''
    description: str = ''
    branch_name: str = ''
    tags: list[str] = field(default_factory=list)
    all_comments: list[dict] = field(default_factory=list)
