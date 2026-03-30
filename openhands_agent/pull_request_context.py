from __future__ import annotations

from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import (
    ImplementationFields,
    PullRequestFields,
    TaskFields,
)
from openhands_agent.text_utils import normalized_text, text_from_mapping


def build_pull_request_context(
    repository_id: str,
    branch_name: str,
    session_id: str = '',
    task_id: str = '',
    task_summary: str = '',
) -> dict[str, str]:
    context = {
        PullRequestFields.REPOSITORY_ID: normalized_text(repository_id),
        Task.branch_name.key: normalized_text(branch_name),
    }
    normalized_session_id = normalized_text(session_id)
    normalized_task_id = normalized_text(task_id)
    normalized_task_summary = normalized_text(task_summary)
    if normalized_session_id:
        context[ImplementationFields.SESSION_ID] = normalized_session_id
    if normalized_task_id:
        context[TaskFields.ID] = normalized_task_id
    if normalized_task_summary:
        context[TaskFields.SUMMARY] = normalized_task_summary
    return context


def normalize_pull_request_context(
    context: object,
    *,
    pull_request_id: str = '',
) -> dict[str, str] | None:
    if not isinstance(context, dict):
        return None
    normalized_context = build_pull_request_context(
        text_from_mapping(context, PullRequestFields.REPOSITORY_ID),
        text_from_mapping(context, Task.branch_name.key),
        text_from_mapping(context, ImplementationFields.SESSION_ID),
        text_from_mapping(context, TaskFields.ID),
        text_from_mapping(context, TaskFields.SUMMARY),
    )
    repository_id, branch_name = pull_request_context_key(normalized_context)
    if not repository_id or not branch_name:
        return None
    normalized_pull_request_id = normalized_text(pull_request_id)
    if normalized_pull_request_id:
        normalized_context[PullRequestFields.ID] = normalized_pull_request_id
    return normalized_context


def pull_request_context_key(context: object) -> tuple[str, str]:
    if not isinstance(context, dict):
        return '', ''
    return (
        text_from_mapping(context, PullRequestFields.REPOSITORY_ID),
        text_from_mapping(context, Task.branch_name.key),
    )
