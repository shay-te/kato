from bitbucket_core_lib.bitbucket_core_lib import BitbucketCoreLib
from kato_core_lib.client.gitlab.issues_client import GitLabIssuesClient
from kato_core_lib.client.jira.issues_client import JiraClient
from kato_core_lib.client.youtrack.issues_client import YouTrackClient
from omegaconf import OmegaConf

from github_core_lib.github_core_lib import GitHubCoreLib


def build_ticket_client(issue_platform: str, config, max_retries: int):
    normalized = str(issue_platform or 'youtrack').strip().lower()
    if normalized == 'youtrack':
        return YouTrackClient(config.base_url, config.token, max_retries)
    if normalized == 'jira':
        return JiraClient(
            config.base_url,
            config.token,
            getattr(config, 'email', ''),
            max_retries,
        )
    if normalized in {'bitbucket', 'bitbucket_issues'}:
        bitbucket_config = OmegaConf.create(
            {
                'core-lib': {
                    'bitbucket-core-lib': {
                        'base_url': config.base_url,
                        'token': config.token,
                        'username': getattr(config, 'username', ''),
                        'api_email': getattr(config, 'api_email', ''),
                        'workspace': getattr(config, 'workspace', ''),
                        'repo_slug': getattr(config, 'repo_slug', ''),
                        'max_retries': max_retries,
                    },
                },
            }
        )
        return BitbucketCoreLib(bitbucket_config).issue
    if normalized in {'github', 'github_issues'}:
        github_config = OmegaConf.create(
            {
                'core-lib': {
                    'github-core-lib': OmegaConf.merge(
                        config,
                        {'max_retries': max_retries},
                    ),
                },
            }
        )
        return GitHubCoreLib(github_config).issue
    if normalized in {'gitlab', 'gitlab_issues'}:
        return GitLabIssuesClient(
            config.base_url,
            config.token,
            getattr(config, 'project', ''),
            max_retries,
        )
    raise ValueError(f'unsupported issue platform: {issue_platform}')
