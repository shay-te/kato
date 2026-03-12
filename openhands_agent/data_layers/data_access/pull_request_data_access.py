from omegaconf import DictConfig

from core_lib.rule_validator.rule_validator import RuleValidator, ValueRuleValidator

from openhands_agent.client.pull_request_client_base import PullRequestClientBase
from openhands_agent.fields import PullRequestFields


pull_request_rule_validator = RuleValidator(
    [
        ValueRuleValidator(PullRequestFields.TITLE, str),
        ValueRuleValidator(PullRequestFields.SOURCE_BRANCH, str),
        ValueRuleValidator(PullRequestFields.DESTINATION_BRANCH, (str, type(None))),
        ValueRuleValidator(PullRequestFields.DESCRIPTION, str),
    ]
)


class PullRequestDataAccess:
    def __init__(self, config: DictConfig, client: PullRequestClientBase) -> None:
        self._config = config
        self._client = client

    @property
    def provider_name(self) -> str:
        return getattr(self._client, 'provider_name', 'repository')

    def validate_connection(self) -> None:
        self._client.validate_connection(
            repo_owner=self._config.owner,
            repo_slug=self._config.repo_slug,
        )

    def create_pull_request(
        self,
        title: str,
        source_branch: str,
        destination_branch: str | None = None,
        description: str = '',
    ) -> dict[str, str]:
        pull_request_rule_validator.validate(
            {
                PullRequestFields.TITLE: title,
                PullRequestFields.SOURCE_BRANCH: source_branch,
                PullRequestFields.DESTINATION_BRANCH: destination_branch,
                PullRequestFields.DESCRIPTION: description,
            }
        )
        return self._client.create_pull_request(
            title=title,
            source_branch=source_branch,
            repo_owner=self._config.owner,
            repo_slug=self._config.repo_slug,
            destination_branch=destination_branch or self._config.destination_branch,
            description=description,
        )
