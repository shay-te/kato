from __future__ import annotations

from typing import Any, Callable

from core_lib.error_handling.not_found_decorator import NotFoundErrorHandler
from omegaconf import DictConfig, OmegaConf

from repository_core_lib.repository_core_lib.platform import Platform


class PullRequestClientFactory(object):
    """Build repository pull-request clients on demand.

    Provider core-libs (github, gitlab, bitbucket) are resolved lazily via the
    default factory helpers below.  Pass explicit callables to
    ``github_client_factory`` / ``gitlab_client_factory`` /
    ``bitbucket_client_factory`` to override the defaults — useful for testing
    or to swap in alternative implementations without touching this module.
    """

    def __init__(
        self,
        config: DictConfig,
        max_retries: int,
        *,
        github_client_factory: Callable[[DictConfig], Any] | None = None,
        gitlab_client_factory: Callable[[DictConfig], Any] | None = None,
        bitbucket_client_factory: Callable[[DictConfig], Any] | None = None,
    ) -> None:
        self._config = config
        self._max_retries = max_retries
        self._github_client_factory = github_client_factory or _default_github_factory
        self._gitlab_client_factory = gitlab_client_factory or _default_gitlab_factory
        self._bitbucket_client_factory = bitbucket_client_factory or _default_bitbucket_factory

    @NotFoundErrorHandler('unsupported repository provider')
    def get(self, platform: Platform) -> Any | None:
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
            return self._bitbucket_client_factory(bitbucket_config)
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
            return self._github_client_factory(github_config)
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
            return self._gitlab_client_factory(gitlab_config)
        return None


def _default_github_factory(config: DictConfig) -> Any:
    from github_core_lib.github_core_lib.github_core_lib import GitHubCoreLib  # noqa: PLC0415
    return GitHubCoreLib(config).pull_request


def _default_gitlab_factory(config: DictConfig) -> Any:
    from gitlab_core_lib.gitlab_core_lib.gitlab_core_lib import GitLabCoreLib  # noqa: PLC0415
    return GitLabCoreLib(config).pull_request


def _default_bitbucket_factory(config: DictConfig) -> Any:
    from bitbucket_core_lib.bitbucket_core_lib.bitbucket_core_lib import BitbucketCoreLib  # noqa: PLC0415
    return BitbucketCoreLib(config).pull_request
