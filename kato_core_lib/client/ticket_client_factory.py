from bitbucket_core_lib.bitbucket_core_lib.bitbucket_core_lib import BitbucketCoreLib
from jira_core_lib.jira_core_lib.jira_core_lib import JiraCoreLib
from gitlab_core_lib.gitlab_core_lib.gitlab_core_lib import GitLabCoreLib
from kato_core_lib.client.youtrack.issues_client import YouTrackClient
from omegaconf import OmegaConf

from github_core_lib.github_core_lib.github_core_lib import GitHubCoreLib


def build_ticket_client(issue_platform: str, config, max_retries: int):
    normalized = str(issue_platform or 'youtrack').strip().lower()
    if normalized == 'youtrack':
        return YouTrackClient(config.base_url, config.token, max_retries)
    if normalized == 'jira':
        jira_config = OmegaConf.create(
            {
                'core_lib': {
                    'jira_core_lib': OmegaConf.merge(
                        config,
                        {'max_retries': max_retries},
                    ),
                },
            }
        )
        return JiraCoreLib(jira_config).issue
    if normalized in {'bitbucket', 'bitbucket_issues'}:
        bitbucket_config = OmegaConf.create(
            {
                'core_lib': {
                    'bitbucket_core_lib': {
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
                'core_lib': {
                    'github_core_lib': OmegaConf.merge(
                        config,
                        {'max_retries': max_retries},
                    ),
                },
            }
        )
        return GitHubCoreLib(github_config).issue
    if normalized in {'gitlab', 'gitlab_issues'}:
        gitlab_config = OmegaConf.create(
            {
                'core_lib': {
                    'gitlab_core_lib': OmegaConf.merge(
                        config,
                        {'max_retries': max_retries},
                    ),
                },
            }
        )
        return GitLabCoreLib(gitlab_config).issue
    raise ValueError(f'unsupported issue platform: {issue_platform}')
