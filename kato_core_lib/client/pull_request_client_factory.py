from urllib.parse import urlparse

from omegaconf import DictConfig, OmegaConf

from kato_core_lib.client.bitbucket.client import BitbucketClient
from kato_core_lib.client.gitlab.client import GitLabClient
from kato_core_lib.client.pull_request_client_base import PullRequestClientBase
from github_core_lib.github_core_lib import GitHubCoreLib


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
        return BitbucketClient(
            config.base_url,
            config.token,
            max_retries,
            username=getattr(config, 'api_email', '') or getattr(config, 'username', ''),
        )
    if provider == 'github':
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
        return GitHubCoreLib(github_config).pull_request
    return GitLabClient(config.base_url, config.token, max_retries)
