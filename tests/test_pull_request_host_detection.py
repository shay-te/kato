"""Working out which host a repository's pull requests live on.

Operator report (Windows): "unable to determine pull request provider for
repository transactions", on a repository that pushes without trouble. The
provider came from the words in the remote, so these all failed:

- ``git@work:team/api.git``, an SSH alias: not a host at all;
- ``git@github-work:team/api.git``: recognised, then sent to the API address
  ``https://github-work/api/v3``;
- a self-hosted GitLab, though the operator's own base URL names its host.

And the message read the same for a host kato does not know as for a repository
that had left the list, which need different fixes.

Real folders, real ``git init``, the real ``ssh`` and RepositoryService.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from git_core_lib.git_core_lib.tests.test_ssh_alias_utils import use_ssh_config
from kato_core_lib.data_layers.service.repository_service import RepositoryService
from tests.test_discovered_repository_duplicates import make_repository

_API = {'base_url': '', 'token': 'the-token', 'username': 'bot', 'api_email': 'bot@example.com'}


class HostDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve() / 'dev'
        settings = patch(
            'kato_core_lib.helpers.kato_settings_store_utils.read_kato_settings', return_value={},
        )
        settings.start()
        self.addCleanup(settings.stop)
        env = patch.dict(os.environ, {'KATO_WORKSPACES_ROOT': str(self.root.parent / 'away')})
        env.start()
        self.addCleanup(env.stop)
        use_ssh_config(self, (
            'Host work\n  HostName bitbucket.org\n'
            'Host github-work\n  HostName github.com\n'
            'Host gl\n  HostName git.company.example\n'
        ))

    def repository(self, remote: str, **api_base_urls):
        make_repository(self.root / 'api', remote)
        config = {
            f'{provider}_issues': SimpleNamespace(**{**_API, 'base_url': api_base_urls.get(provider, '')})
            for provider in ('github', 'gitlab', 'bitbucket')
        }
        self.service = RepositoryService(
            SimpleNamespace(repository_root_path=str(self.root), **config), 3,
        )
        return self.service.get_repository('api')

    def prepared(self, remote: str, **api_base_urls):
        repository = self.repository(remote, **api_base_urls)
        self.service._prepare_pull_request_api(repository)
        return repository

    def review_url(self, repository) -> str:
        return self.service._review_url(repository, 'LOCAL-1', 'master')

    # ----- an SSH alias -----

    def test_an_alias_with_no_telling_name_finds_its_host(self) -> None:
        repository = self.prepared('git@work:team/api.git')

        self.assertEqual(repository.provider, 'bitbucket')
        self.assertEqual(repository.provider_base_url, 'https://api.bitbucket.org/2.0')

    def test_an_alias_with_a_telling_name_gets_the_real_api_address(self) -> None:
        repository = self.prepared('git@github-work:team/api.git')

        self.assertEqual(repository.provider, 'github')
        self.assertEqual(repository.provider_base_url, 'https://api.github.com')

    def test_the_review_link_goes_to_the_real_host(self) -> None:
        repository = self.prepared('git@work:team/api.git')

        self.assertTrue(
            self.review_url(repository).startswith('https://bitbucket.org/team/api/'),
            self.review_url(repository),
        )

    def test_the_branch_pull_requests_page_goes_to_the_real_host(self) -> None:
        # The PR list's link when the host's API did not answer: built from the
        # remote alone, through the same host resolution as the review link.
        repository = self.prepared('git@work:team/api.git')

        self.assertEqual(
            self.service.branch_pull_requests_url(repository, 'UNA-3369'),
            'https://bitbucket.org/team/api/pull-requests/?query=UNA-3369',
        )

    def test_the_branch_pull_requests_page_of_a_telling_alias_too(self) -> None:
        github = self.prepared('git@github-work:team/api.git')
        self.assertEqual(
            self.service.branch_pull_requests_url(github, 'UNA-3369'),
            'https://github.com/team/api/pulls?q=is%3Apr%20head%3AUNA-3369',
        )

    def test_the_remote_itself_is_never_rewritten(self) -> None:
        # A push compares it with the clone's own, and refuses on a difference.
        repository = self.prepared('git@work:team/api.git')

        self.assertEqual(repository.remote_url, 'git@work:team/api.git')
        self.assertEqual((repository.owner, repository.repo_slug), ('team', 'api'))

    # ----- a self-hosted host the operator already named -----

    def test_a_base_url_on_the_same_host_says_what_the_host_is(self) -> None:
        repository = self.prepared(
            'git@git.company.example:team/api.git',
            gitlab='https://git.company.example/api/v4',
        )

        self.assertEqual(repository.provider, 'gitlab')
        self.assertEqual(repository.provider_base_url, 'https://git.company.example/api/v4')

    def test_it_works_through_an_alias_too(self) -> None:
        repository = self.prepared(
            'git@gl:team/api.git', gitlab='https://git.company.example/api/v4',
        )

        self.assertEqual(repository.provider, 'gitlab')

    def test_a_base_url_on_another_host_claims_nothing(self) -> None:
        repository = self.repository(
            'git@git.other.example:team/api.git',
            gitlab='https://git.company.example/api/v4',
        )

        with self.assertRaises(ValueError):
            self.service._prepare_pull_request_api(repository)

    # ----- what is said when it cannot tell -----

    def test_an_unknown_host_is_named_with_what_to_set(self) -> None:
        repository = self.repository('git@git.other.example:team/api.git')

        with self.assertRaises(ValueError) as caught:
            self.service._prepare_pull_request_api(repository)

        self.assertIn('unable to determine pull request provider for repository api', str(caught.exception))
        self.assertIn('git.other.example', str(caught.exception))
        self.assertIn('GITLAB_API_BASE_URL', str(caught.exception))

    def test_a_secret_in_the_remote_never_reaches_the_message(self) -> None:
        repository = self.repository('https://bot:hunter2@git.other.example/team/api.git')

        with self.assertRaises(ValueError) as caught:
            self.service._prepare_pull_request_api(repository)

        self.assertIn('git.other.example', str(caught.exception))
        self.assertNotIn('hunter2', str(caught.exception))

    def test_a_remote_that_is_a_folder_is_not_called_a_host(self) -> None:
        repository = self.repository(str(self.root.parent / 'remote' / 'api.git'))

        with self.assertRaises(ValueError) as caught:
            self.service._prepare_pull_request_api(repository)

        self.assertIn('its remote is not a git host', str(caught.exception))

    def test_a_repository_out_of_the_list_says_so(self) -> None:
        # What ``_resolve_publish_context`` hands over for such a repository.
        # Its remote is deliberately not read from the clone: an agent can
        # write that file, and the provider token goes to the address it names.
        service = self.repository('git@git.other.example:team/api.git') and self.service

        with self.assertRaises(ValueError) as caught:
            service._prepare_pull_request_api(SimpleNamespace(id='gone', local_path='/somewhere'))

        self.assertIn('not in the repository list', str(caught.exception))
        self.assertIn('REPOSITORY_ROOT_PATH', str(caught.exception))

    # ----- a plain remote costs nothing -----

    def test_a_public_host_never_asks_ssh(self) -> None:
        with patch(
            'git_core_lib.git_core_lib.helpers.ssh_alias_utils.ssh_host_name',
        ) as lookup:
            repository = self.prepared('git@github.com:team/api.git')

        self.assertEqual(repository.provider_base_url, 'https://api.github.com')
        lookup.assert_not_called()


if __name__ == '__main__':
    unittest.main()
