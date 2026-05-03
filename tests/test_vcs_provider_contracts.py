import inspect
import unittest

from github_core_lib.client.github_client import GitHubClient
from github_core_lib.client.github_issues_client import GitHubIssuesClient
from github_core_lib.github_core_lib import GitHubCoreLib
from omegaconf import OmegaConf
from vcs_provider_contracts.issue import Issue
from vcs_provider_contracts.issue_provider import IssueProvider
from vcs_provider_contracts.pull_request import PullRequest
from vcs_provider_contracts.pull_request_provider import PullRequestProvider
from vcs_provider_contracts.review_comment import ReviewComment


class ContractPullRequestProvider(object):
    def validate_connection(self, repo_owner: str, repo_slug: str) -> None:
        return None

    def create_pull_request(
        self,
        title: str,
        source_branch: str,
        repo_owner: str,
        repo_slug: str,
        destination_branch: str | None = None,
        description: str = '',
    ) -> PullRequest:
        return PullRequest(id='1', title=title, url='https://example.com/pr/1')

    def list_pull_request_comments(
        self,
        repo_owner: str,
        repo_slug: str,
        pull_request_id: str,
    ) -> list[ReviewComment]:
        return [ReviewComment(pull_request_id=pull_request_id)]

    def find_pull_requests(
        self,
        repo_owner: str,
        repo_slug: str,
        *,
        source_branch: str = '',
        title_prefix: str = '',
    ) -> list[PullRequest]:
        return [PullRequest(id='1', title=title_prefix)]

    def reply_to_review_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        comment: ReviewComment,
        body: str,
    ) -> None:
        return None

    def resolve_review_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        comment: ReviewComment,
    ) -> None:
        return None


class ContractIssueProvider(object):
    def validate_connection(self, project: str, assignee: str, states: list[str]) -> None:
        return None

    def get_assigned_tasks(self, project: str, assignee: str, states: list[str]) -> list[Issue]:
        return [Issue(id='ISSUE-1', title='Example')]

    def add_comment(self, issue_id: str, comment: str) -> None:
        return None

    def move_issue_to_state(self, issue_id: str, field_name: str, state_name: str) -> None:
        return None

    def add_tag(self, issue_id: str, label_name: str) -> None:
        return None

    def remove_tag(self, issue_id: str, label_name: str) -> None:
        return None


class VcsProviderContractsTests(unittest.TestCase):
    def test_pull_request_contract_runtime_check_accepts_matching_provider(self) -> None:
        self.assertIsInstance(ContractPullRequestProvider(), PullRequestProvider)
        self.assertIsInstance(GitHubClient('https://api.github.com', 'gh-token'), PullRequestProvider)

    def test_issue_contract_runtime_check_accepts_matching_provider(self) -> None:
        self.assertIsInstance(ContractIssueProvider(), IssueProvider)
        self.assertIsInstance(
            GitHubIssuesClient('https://api.github.com', 'gh-token', 'workspace', 'repo'),
            IssueProvider,
        )

    def test_pull_request_provider_signature_names_are_stable(self) -> None:
        self.assertEqual(
            list(inspect.signature(PullRequestProvider.create_pull_request).parameters),
            [
                'self',
                'title',
                'source_branch',
                'repo_owner',
                'repo_slug',
                'destination_branch',
                'description',
            ],
        )
        self.assertEqual(
            list(inspect.signature(PullRequestProvider.find_pull_requests).parameters),
            ['self', 'repo_owner', 'repo_slug', 'source_branch', 'title_prefix'],
        )

    def test_issue_provider_signature_names_are_stable(self) -> None:
        self.assertEqual(
            list(inspect.signature(IssueProvider.get_assigned_tasks).parameters),
            ['self', 'project', 'assignee', 'states'],
        )
        self.assertEqual(
            list(inspect.signature(IssueProvider.move_issue_to_state).parameters),
            ['self', 'issue_id', 'field_name', 'state_name'],
        )

    def test_contract_records_are_provider_neutral(self) -> None:
        pull_request = PullRequest(id='17', title='Fix', url='https://example.com/pr/17')
        comment = ReviewComment(
            pull_request_id='17',
            comment_id='99',
            author='reviewer',
            body='Please fix',
            resolution_target_id='thread-1',
            resolution_target_type='thread',
            resolvable=True,
        )
        issue = Issue(id='ISSUE-1', title='Task', body='Body', state='open', labels=('bug',))

        self.assertEqual(pull_request.id, '17')
        self.assertEqual(comment.resolution_target_id, 'thread-1')
        self.assertEqual(issue.labels, ('bug',))

    def test_github_core_lib_composes_both_clients(self) -> None:
        cfg = OmegaConf.create(
            {
                'core-lib': {
                    'github-core-lib': {
                        'base_url': 'https://api.github.com',
                        'token': 'gh-token',
                        'owner': 'octo',
                        'repo': 'repo',
                        'max_retries': 3,
                    },
                },
            }
        )
        github = GitHubCoreLib(cfg)

        self.assertIsInstance(github.pull_request, GitHubClient)
        self.assertIsInstance(github.issue, GitHubIssuesClient)
        self.assertEqual(github.pull_request.max_retries, 3)
        self.assertEqual(github.issue.max_retries, 3)

    def test_github_core_lib_accepts_repository_config_slug_name(self) -> None:
        cfg = OmegaConf.create(
            {
                'core-lib': {
                    'github-core-lib': {
                        'base_url': 'https://api.github.com',
                        'token': 'gh-token',
                        'owner': 'octo',
                        'repo_slug': 'repo',
                        'max_retries': 4,
                    },
                },
            }
        )
        github = GitHubCoreLib(cfg)

        self.assertIsInstance(github.pull_request, GitHubClient)
        self.assertIsInstance(github.issue, GitHubIssuesClient)
        self.assertEqual(github.issue.max_retries, 4)
