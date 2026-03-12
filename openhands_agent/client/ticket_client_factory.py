from openhands_agent.client.jira_client import JiraClient
from openhands_agent.client.youtrack_client import YouTrackClient


def build_ticket_client(ticket_system: str, config, max_retries: int):
    normalized = str(ticket_system or 'youtrack').strip().lower()
    if normalized == 'youtrack':
        return YouTrackClient(config.base_url, config.token, max_retries)
    if normalized == 'jira':
        return JiraClient(
            config.base_url,
            config.token,
            getattr(config, 'email', ''),
            max_retries,
        )
    raise ValueError(f'unsupported ticket system: {ticket_system}')
