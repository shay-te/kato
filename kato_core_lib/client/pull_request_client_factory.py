from urllib.parse import urlparse

from omegaconf import DictConfig, OmegaConf

from bitbucket_core_lib.bitbucket_core_lib.bitbucket_core_lib import BitbucketCoreLib
from gitlab_core_lib.gitlab_core_lib.gitlab_core_lib import GitLabCoreLib
from kato_core_lib.client.pull_request_client_base import PullRequestClientBase
from github_core_lib.github_core_lib.github_core_lib import GitHubCoreLib


def detect_pull_request_provider(base_url: str) -> str:
    parsed = urlparse(base_url)
    target = f'{parsed.netloc}{parsed.path}'.lower()
    if 'github' in target:
        return 'github'
    if 'gitlab' in target:
        return 'gitlab'
    if 'bitbucket' in target:
        return 'bitbucket'
    raise ValueError(f'unsupported repository provider for base_url: {base_url}')


def build_pull_request_client(
    config: DictConfig,
    max_retries: int,
) -> PullRequestClientBase:
    provider = detect_pull_request_provider(config.base_url)
    if provider == 'bitbucket':
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
        return BitbucketCoreLib(bitbucket_config).pull_request
    if provider == 'github':
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
        return GitHubCoreLib(github_config).pull_request
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
    return GitLabCoreLib(gitlab_config).pull_request
