"""``RepositoryService.pull_request_states`` — every PR of a branch, any state.

The scan's ``find_pull_requests`` answers ``[]`` when the pull request API
cannot be prepared (a dependency repo with no token is not an error there).
A merge check cannot read it that way: ``[]`` means "this branch has no pull
request", so here "could not ask" must RAISE, never look like an answer.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_core_lib.data_layers.service.repository_service import RepositoryService
from tests.utils import build_test_cfg


def _service_with_data_access() -> tuple[RepositoryService, MagicMock]:
    service = RepositoryService(build_test_cfg(), 3)
    service._prepare_pull_request_api = MagicMock()
    data_access = MagicMock()
    service._pull_request_data_access = MagicMock(return_value=data_access)
    return service, data_access


class PullRequestStatesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = SimpleNamespace(id='client')

    def test_returns_the_branchs_pull_requests_in_every_state(self) -> None:
        service, data_access = _service_with_data_access()
        merged = {
            'id': '17', 'title': 'PROJ-1 fix', 'url': 'https://example.com/pr/17',
            'state': 'merged', 'destination_branch': 'main',
            'created_at': '2026-03-01T10:00:00Z',
        }
        declined = {**merged, 'id': '12', 'state': 'declined'}
        data_access.find_pull_requests.return_value = [merged, declined]

        states = service.pull_request_states(self.repository, source_branch=' feature/proj-1 ')

        self.assertEqual(states, [merged, declined])
        service._prepare_pull_request_api.assert_called_once_with(self.repository)
        service._pull_request_data_access.assert_called_once_with(self.repository)
        data_access.find_pull_requests.assert_called_once_with(
            source_branch='feature/proj-1',
            state='all',
        )

    def test_no_pull_requests_is_an_empty_list(self) -> None:
        service, data_access = _service_with_data_access()
        data_access.find_pull_requests.return_value = []

        self.assertEqual(
            service.pull_request_states(self.repository, source_branch='feature/proj-1'),
            [],
        )

    def test_raises_when_the_pull_request_api_cannot_be_prepared(self) -> None:
        service, data_access = _service_with_data_access()
        service._prepare_pull_request_api.side_effect = ValueError(
            'missing pull request token for repository client',
        )

        with self.assertRaisesRegex(ValueError, 'missing pull request token'):
            service.pull_request_states(self.repository, source_branch='feature/proj-1')

        data_access.find_pull_requests.assert_not_called()

    def test_the_same_failure_is_an_empty_answer_on_the_scan_lookup(self) -> None:
        # Pins the difference: the scan's lookup swallows what this raises.
        service, data_access = _service_with_data_access()
        service._prepare_pull_request_api.side_effect = ValueError('missing token')

        self.assertEqual(
            service.find_pull_requests(self.repository, source_branch='feature/proj-1'),
            [],
        )
        with self.assertRaises(ValueError):
            service.pull_request_states(self.repository, source_branch='feature/proj-1')

    def test_raises_when_the_pull_request_configuration_is_incomplete(self) -> None:
        service, _ = _service_with_data_access()
        service._pull_request_data_access.side_effect = ValueError(
            'incomplete pull request configuration for repository client',
        )

        with self.assertRaisesRegex(ValueError, 'incomplete pull request configuration'):
            service.pull_request_states(self.repository, source_branch='feature/proj-1')

    def test_raises_when_the_provider_call_fails(self) -> None:
        service, data_access = _service_with_data_access()
        data_access.find_pull_requests.side_effect = RuntimeError('503 Service Unavailable')

        with self.assertRaisesRegex(RuntimeError, '503'):
            service.pull_request_states(self.repository, source_branch='feature/proj-1')

    def test_a_blank_branch_is_refused_rather_than_listing_every_pull_request(self) -> None:
        service, data_access = _service_with_data_access()

        for branch in ('', '   '):
            with self.subTest(branch=branch):
                with self.assertRaisesRegex(ValueError, 'source branch is required'):
                    service.pull_request_states(self.repository, source_branch=branch)

        service._prepare_pull_request_api.assert_not_called()
        data_access.find_pull_requests.assert_not_called()


if __name__ == '__main__':
    unittest.main()
