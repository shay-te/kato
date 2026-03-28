import unittest


from openhands_agent.client.bitbucket_client import BitbucketClient
from openhands_agent.client.github_client import GitHubClient
from openhands_agent.client.gitlab_client import GitLabClient
from openhands_agent.client.pull_request_client_base import PullRequestClientBase


class PullRequestClientBaseTests(unittest.TestCase):
    def test_cannot_instantiate_abstract_base_directly(self) -> None:
        with self.assertRaises(TypeError):
            PullRequestClientBase('https://example.com', 'token', timeout=30)

    def test_all_repository_clients_implement_shared_base_contract(self) -> None:
        self.assertTrue(issubclass(BitbucketClient, PullRequestClientBase))
        self.assertTrue(issubclass(GitHubClient, PullRequestClientBase))
        self.assertTrue(issubclass(GitLabClient, PullRequestClientBase))
