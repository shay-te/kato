from __future__ import annotations

from bitbucket_core_lib.bitbucket_core_lib.bitbucket_core_lib import BitbucketCoreLib
from core_lib.error_handling.not_found_decorator import NotFoundErrorHandler
from gitlab_core_lib.gitlab_core_lib.gitlab_core_lib import GitLabCoreLib
from github_core_lib.github_core_lib.github_core_lib import GitHubCoreLib
from jira_core_lib.jira_core_lib.jira_core_lib import JiraCoreLib
from omegaconf import OmegaConf
from youtrack_core_lib.youtrack_core_lib.youtrack_core_lib import YouTrackCoreLib

from task_core_lib.task_core_lib.platform import Platform
from vcs_provider_contracts.vcs_provider_contracts.issue_provider import IssueProvider


class TaskClientFactory(object):
    """Build issue providers for the configured task platform."""

    def __init__(self, config, max_retries: int) -> None:
        self._config = config
        self._max_retries = max_retries

    @NotFoundErrorHandler('unsupported issue platform')
    def get(self, platform: Platform) -> IssueProvider | None:
        if platform == Platform.YOUTRACK:
            # Resolve interpolations before wrapping to avoid circular references.
            config_dict = OmegaConf.to_container(self._config, resolve=True)
            youtrack_config = OmegaConf.create(
                {
                    'core_lib': {
                        'youtrack_core_lib': OmegaConf.merge(
                            config_dict,
                            {'max_retries': self._max_retries},
                        ),
                    },
                }
            )
            return YouTrackCoreLib(youtrack_config).issue
        if platform == Platform.JIRA:
            jira_config = OmegaConf.create(
                {
                    'core_lib': {
                        'jira_core_lib': OmegaConf.merge(
                            self._config,
                            {'max_retries': self._max_retries},
                        ),
                    },
                }
            )
            return JiraCoreLib(jira_config).issue
        if platform in {Platform.BITBUCKET, Platform.BITBUCKET_ISSUES}:
            bitbucket_config = OmegaConf.create(
                {
                    'core_lib': {
                        'bitbucket_core_lib': {
                            'base_url': self._config.base_url,
                            'token': self._config.token,
                            'username': getattr(self._config, 'username', ''),
                            'api_email': getattr(self._config, 'api_email', ''),
                            'workspace': getattr(self._config, 'workspace', ''),
                            'repo_slug': getattr(self._config, 'repo_slug', ''),
                            'max_retries': self._max_retries,
                        },
                    },
                }
            )
            return BitbucketCoreLib(bitbucket_config).issue
        if platform in {Platform.GITHUB, Platform.GITHUB_ISSUES}:
            github_config = OmegaConf.create(
                {
                    'core_lib': {
                        'github_core_lib': OmegaConf.merge(
                            self._config,
                            {'max_retries': self._max_retries},
                        ),
                    },
                }
            )
            return GitHubCoreLib(github_config).issue
        if platform in {Platform.GITLAB, Platform.GITLAB_ISSUES}:
            gitlab_config = OmegaConf.create(
                {
                    'core_lib': {
                        'gitlab_core_lib': OmegaConf.merge(
                            self._config,
                            {'max_retries': self._max_retries},
                        ),
                    },
                }
            )
            return GitLabCoreLib(gitlab_config).issue
        return None
