from omegaconf import DictConfig

from openhands_agent.client.bitbucket_client import BitbucketClient


class PullRequestDataAccess:
    def __init__(self, config: DictConfig, client: BitbucketClient) -> None:
        self.config = config
        self.client = client

    def create_pull_request(
        self,
        title: str,
        source_branch: str,
        destination_branch: str | None = None,
        description: str = '',
    ) -> dict[str, str]:
        return self.client.create_pull_request(
            title=title,
            source_branch=source_branch,
            workspace=self.config.workspace,
            repo_slug=self.config.repo_slug,
            destination_branch=destination_branch or self.config.destination_branch,
            description=description,
        )
