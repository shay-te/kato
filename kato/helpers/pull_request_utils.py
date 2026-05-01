from __future__ import annotations

from kato.data_layers.data.fields import ImplementationFields, PullRequestFields
from kato.data_layers.data.task import Task
from kato.helpers.text_utils import text_from_mapping


def pull_request_repositories_text(pull_requests) -> str:
    if not isinstance(pull_requests, list):
        return '<none>'
    repository_ids = [
        text_from_mapping(pull_request, PullRequestFields.REPOSITORY_ID)
        for pull_request in pull_requests
        if isinstance(pull_request, dict)
    ]
    repository_ids = [repository_id for repository_id in repository_ids if repository_id]
    return ', '.join(repository_ids) if repository_ids else '<none>'


def pull_request_title(task: Task) -> str:
    task_id = str(task.id or '').strip()
    task_summary = str(task.summary or '').strip()
    if task_id and task_summary:
        return f'{task_id} {task_summary}'
    return task_id or task_summary or 'Kato task'


def pull_request_summary_comment(
    task: Task,
    pull_requests: list[dict[str, str]],
    failed_repositories,
    execution_report: str = '',
) -> str:
    """Render the YouTrack/Jira summary comment.

    ``failed_repositories`` accepts two shapes:

    * ``list[str]`` (legacy) — just the repo ids; the comment lists
      them with no per-repo error reason. Kept for backward compat.
    * ``list[tuple[str, str]] | list[dict]`` — per-repo error info; the
      comment includes the failure reason on each line so the operator
      can act without spelunking through kato logs. ``dict`` form
      matches the publish-result payload (``{repository_id, error}``).
    """
    lines = [f'Kato completed task {task.id}: {task.summary}.']
    if execution_report:
        lines.append('')
        lines.append('Execution report:')
        lines.append(execution_report)
    if pull_requests:
        lines.append('')
        lines.append('Published review links:')
        for pull_request in pull_requests:
            lines.append(
                f'- {pull_request[PullRequestFields.REPOSITORY_ID]}: '
                f'{pull_request[PullRequestFields.URL]}'
            )
    failure_lines = _failed_repository_lines(failed_repositories)
    if failure_lines:
        lines.append('')
        lines.append('Failed repositories:')
        lines.extend(failure_lines)
    return '\n'.join(lines)


def _failed_repository_lines(failed_repositories) -> list[str]:
    if not failed_repositories:
        return []
    rendered: list[str] = []
    for entry in failed_repositories:
        repo_id, reason = _coerce_failed_repo_entry(entry)
        if not repo_id:
            continue
        if reason:
            rendered.append(f'- {repo_id}: {reason}')
        else:
            rendered.append(f'- {repo_id}')
    return rendered


def _coerce_failed_repo_entry(entry) -> tuple[str, str]:
    if isinstance(entry, str):
        return entry, ''
    if isinstance(entry, tuple) and len(entry) == 2:
        return str(entry[0] or ''), str(entry[1] or '')
    if isinstance(entry, dict):
        repo_id = str(
            entry.get(PullRequestFields.REPOSITORY_ID, '')
            or entry.get('repository_id', '')
            or '',
        )
        reason = str(entry.get('error', '') or entry.get('reason', '') or '')
        return repo_id, reason
    return str(entry or ''), ''


def pull_request_description(
    task: Task,
    execution: dict[str, str | bool],
) -> str:
    lines = [f'Kato completed task {task.id}: {task.summary}.']
    task_description = str(task.description or '').strip()
    if task_description:
        lines.append('')
        lines.append('Requested change:')
        lines.append(task_description)

    implementation_summary = str(execution.get(Task.summary.key, '') or '').strip()
    if implementation_summary:
        lines.append('')
        lines.append('Implementation summary:')
        lines.append(implementation_summary)

    execution_notes = str(execution.get(ImplementationFields.MESSAGE, '') or '').strip()
    if execution_notes:
        lines.append('')
        lines.append('Execution notes:')
        lines.append(execution_notes)

    return '\n'.join(lines)
