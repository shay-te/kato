from __future__ import annotations

from typing import Protocol, runtime_checkable

from vcs_provider_contracts.vcs_provider_contracts.issue import Issue


@runtime_checkable
class IssueProvider(Protocol):
    def validate_connection(self, project: str, assignee: str, states: list[str]) -> None:
        raise NotImplementedError

    def get_assigned_tasks(self, project: str, assignee: str, states: list[str]) -> list[Issue]:
        raise NotImplementedError

    def add_comment(self, issue_id: str, comment: str) -> None:
        raise NotImplementedError

    def move_issue_to_state(self, issue_id: str, field_name: str, state_name: str) -> None:
        raise NotImplementedError

    def add_tag(self, issue_id: str, label_name: str) -> None:
        raise NotImplementedError

    def remove_tag(self, issue_id: str, label_name: str) -> None:
        raise NotImplementedError
