from __future__ import annotations

import unittest
from unittest.mock import Mock

from repository_core_lib.repository_core_lib.pull_request_service import PullRequestService
from repository_core_lib.repository_core_lib.repository_type import RepositoryType


class PullRequestServiceTests(unittest.TestCase):
    def test_validate_connection_routes_github_client(self) -> None:
        service, factory, client = self._service_with_client()

        service.validate_connection(
            RepositoryType.GITHUB,
            repo_owner='octo',
            repo_slug='repo',
        )

        factory.get.assert_called_once_with(RepositoryType.GITHUB)
        client.validate_connection.assert_called_once_with(
            repo_owner='octo',
            repo_slug='repo',
        )

    def test_validate_connection_routes_gitlab_client(self) -> None:
        service, factory, client = self._service_with_client()

        service.validate_connection(
            RepositoryType.GITLAB,
            repo_owner='group',
            repo_slug='repo',
        )

        factory.get.assert_called_once_with(RepositoryType.GITLAB)
        client.validate_connection.assert_called_once_with(
            repo_owner='group',
            repo_slug='repo',
        )

    def test_validate_connection_routes_bitbucket_client(self) -> None:
        service, factory, client = self._service_with_client()

        service.validate_connection(
            RepositoryType.BITBUCKET,
            repo_owner='workspace',
            repo_slug='repo',
        )

        factory.get.assert_called_once_with(RepositoryType.BITBUCKET)
        client.validate_connection.assert_called_once_with(
            repo_owner='workspace',
            repo_slug='repo',
        )

    def test_create_pull_request_routes_to_client(self) -> None:
        service, factory, client = self._service_with_client()
        client.create_pull_request.return_value = {'id': '17'}

        result = service.create_pull_request(
            RepositoryType.GITHUB,
            title='PROJ-1: Fix bug',
            source_branch='feature/proj-1',
            repo_owner='octo',
            repo_slug='repo',
            destination_branch='main',
            description='Ready for review',
        )

        self.assertEqual(result, {'id': '17'})
        factory.get.assert_called_once_with(RepositoryType.GITHUB)
        client.create_pull_request.assert_called_once_with(
            title='PROJ-1: Fix bug',
            source_branch='feature/proj-1',
            repo_owner='octo',
            repo_slug='repo',
            destination_branch='main',
            description='Ready for review',
        )

    def test_list_pull_request_comments_routes_to_client(self) -> None:
        service, factory, client = self._service_with_client()
        client.list_pull_request_comments.return_value = ['comment']

        comments = service.list_pull_request_comments(
            RepositoryType.GITLAB,
            repo_owner='group',
            repo_slug='repo',
            pull_request_id='17',
        )

        self.assertEqual(comments, ['comment'])
        factory.get.assert_called_once_with(RepositoryType.GITLAB)
        client.list_pull_request_comments.assert_called_once_with(
            repo_owner='group',
            repo_slug='repo',
            pull_request_id='17',
        )

    def test_find_pull_requests_routes_to_client(self) -> None:
        service, factory, client = self._service_with_client()
        client.find_pull_requests.return_value = ['pr']

        pull_requests = service.find_pull_requests(
            RepositoryType.BITBUCKET,
            repo_owner='workspace',
            repo_slug='repo',
            source_branch='feature/proj-1',
            title_prefix='PROJ-1',
        )

        self.assertEqual(pull_requests, ['pr'])
        factory.get.assert_called_once_with(RepositoryType.BITBUCKET)
        client.find_pull_requests.assert_called_once_with(
            repo_owner='workspace',
            repo_slug='repo',
            source_branch='feature/proj-1',
            title_prefix='PROJ-1',
        )

    def test_resolve_review_comment_routes_to_client(self) -> None:
        service, factory, client = self._service_with_client()
        comment = Mock()

        service.resolve_review_comment(
            RepositoryType.BITBUCKET,
            repo_owner='workspace',
            repo_slug='repo',
            comment=comment,
        )

        factory.get.assert_called_once_with(RepositoryType.BITBUCKET)
        client.resolve_review_comment.assert_called_once_with(
            repo_owner='workspace',
            repo_slug='repo',
            comment=comment,
        )

    def test_reply_to_review_comment_routes_to_client(self) -> None:
        service, factory, client = self._service_with_client()
        comment = Mock()

        service.reply_to_review_comment(
            RepositoryType.GITHUB,
            repo_owner='octo',
            repo_slug='repo',
            comment=comment,
            body='Done.',
        )

        factory.get.assert_called_once_with(RepositoryType.GITHUB)
        client.reply_to_review_comment.assert_called_once_with(
            repo_owner='octo',
            repo_slug='repo',
            comment=comment,
            body='Done.',
        )

    def test_builds_a_fresh_client_for_each_repository_type_call(self) -> None:
        service, factory, github_client = self._service_with_client()
        bitbucket_client = Mock()
        factory.get.side_effect = [github_client, bitbucket_client]

        service.validate_connection(
            RepositoryType.GITHUB,
            repo_owner='octo',
            repo_slug='repo',
        )
        service.validate_connection(
            RepositoryType.BITBUCKET,
            repo_owner='octo',
            repo_slug='repo',
        )

        factory.get.assert_any_call(RepositoryType.GITHUB)
        factory.get.assert_any_call(RepositoryType.BITBUCKET)
        self.assertEqual(factory.get.call_count, 2)

    @staticmethod
    def _service_with_client() -> tuple[PullRequestService, Mock, Mock]:
        factory = Mock()
        client = Mock()
        factory.get.return_value = client
        service = PullRequestService(factory)
        return service, factory, client
