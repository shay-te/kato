from __future__ import annotations

from typing import Protocol, runtime_checkable

from vcs_provider_contracts.issue import Issue
from vcs_provider_contracts.issue_comment import IssueComment


@runtime_checkable
class IssueProvider(Protocol):
    def validate_issue_access(self, project: str, assignee: str, states: list[str]) -> None:
        raise NotImplementedError

    def get_assigned_issues(self, project: str, assignee: str, states: list[str]) -> list[Issue]:
        raise NotImplementedError

    def list_issue_comments(self, issue_id: str) -> list[IssueComment]:
        raise NotImplementedError

    def add_issue_comment(self, issue_id: str, comment: str) -> None:
        raise NotImplementedError

    def move_issue_to_state(self, issue_id: str, field_name: str, state_name: str) -> None:
        raise NotImplementedError

    def add_issue_label(self, issue_id: str, label_name: str) -> None:
        raise NotImplementedError

    def remove_issue_label(self, issue_id: str, label_name: str) -> None:
        raise NotImplementedError
