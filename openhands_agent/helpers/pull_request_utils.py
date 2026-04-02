from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.data.fields import PullRequestFields
from openhands_agent.helpers.text_utils import text_from_mapping


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


def pull_request_summary_comment(
    task: Task,
    pull_requests: list[dict[str, str]],
    failed_repositories: list[str],
    execution_report: str = '',
) -> str:
    lines = [f'OpenHands completed task {task.id}: {task.summary}.']
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
    if failed_repositories:
        lines.append('')
        lines.append('Failed repositories: ' + ', '.join(failed_repositories))
    return '\n'.join(lines)
