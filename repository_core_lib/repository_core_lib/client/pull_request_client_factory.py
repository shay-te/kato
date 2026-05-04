from __future__ import annotations

from bitbucket_core_lib.bitbucket_core_lib.bitbucket_core_lib import BitbucketCoreLib
from gitlab_core_lib.gitlab_core_lib.gitlab_core_lib import GitLabCoreLib
from github_core_lib.github_core_lib.github_core_lib import GitHubCoreLib
from kato_core_lib.client.pull_request_client_base import PullRequestClientBase
from core_lib.error_handling.not_found_decorator import NotFoundErrorHandler
from omegaconf import DictConfig, OmegaConf
from repository_core_lib.repository_core_lib.platform import Platform


class PullRequestClientFactory(object):
    """Build repository pull-request clients on demand."""

    def __init__(self, config: DictConfig, max_retries: int) -> None:
        self._config = config
        self._max_retries = max_retries

    @NotFoundErrorHandler('unsupported repository provider')
    def get(self, platform: Platform) -> PullRequestClientBase | None:
        if platform == Platform.BITBUCKET:
            bitbucket_config = OmegaConf.create(
                {
                    'core_lib': {
                        'bitbucket_core_lib': {
                            'base_url': self._config.base_url,
                            'token': self._config.token,
                            'username': self._config.get('username', ''),
                            'api_email': self._config.get('api_email', ''),
                            'workspace': self._config.get('workspace', ''),
                            'repo_slug': self._config.get('repo_slug', ''),
                            'max_retries': self._max_retries,
                        },
                    },
                }
            )
            return BitbucketCoreLib(bitbucket_config).pull_request
        if platform == Platform.GITHUB:
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
            return GitHubCoreLib(github_config).pull_request
        if platform == Platform.GITLAB:
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
            return GitLabCoreLib(gitlab_config).pull_request
        return None
