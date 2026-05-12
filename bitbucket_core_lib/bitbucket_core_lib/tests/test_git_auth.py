"""Coverage for ``bitbucket_core_lib/helpers/git_auth.py``.

These helpers were extracted from kato's ``RepositoryService`` so any
provider core-lib could reuse them. Both functions are pure (no I/O),
so the tests just verify the dispatch matrix: bitbucket vs other
providers, URL-embedded usernames vs config, and the no-token /
no-username edge cases.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from bitbucket_core_lib.bitbucket_core_lib.helpers.git_auth import (
    BITBUCKET_USERNAME_ATTR,
    git_http_auth_header,
    git_http_username,
)


def _repo(**kwargs):
    defaults = {
        'remote_url': '', 'provider': '', 'token': '',
        'username': '', BITBUCKET_USERNAME_ATTR: '',
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class GitHttpAuthHeaderTests(unittest.TestCase):
    def test_returns_empty_when_repository_is_none(self) -> None:
        self.assertEqual(git_http_auth_header(None), '')

    def test_returns_empty_when_remote_is_ssh(self) -> None:
        self.assertEqual(
            git_http_auth_header(_repo(remote_url='git@github.com:org/repo.git')),
            '',
        )

    def test_returns_empty_when_token_missing(self) -> None:
        # Line 39: ``if not token: return ''``.
        self.assertEqual(
            git_http_auth_header(_repo(
                remote_url='https://github.com/org/repo.git',
                provider='github',
            )),
            '',
        )

    def test_returns_empty_when_username_resolves_blank(self) -> None:
        # Line 44: ``if not username: return ''`` — defensive branch.
        # Force the username helper to return '' via patch.
        from unittest.mock import patch
        with patch(
            'bitbucket_core_lib.bitbucket_core_lib.helpers.git_auth.git_http_username',
            return_value='',
        ):
            result = git_http_auth_header(_repo(
                remote_url='https://github.com/org/repo.git',
                provider='github',
                token='gh-token',
            ))
        self.assertEqual(result, '')

    def test_returns_header_for_github_https(self) -> None:
        header = git_http_auth_header(_repo(
            remote_url='https://github.com/org/repo.git',
            provider='github',
            token='gh-token',
        ))
        self.assertTrue(header.startswith('Authorization: Basic '))


class GitHttpUsernameTests(unittest.TestCase):
    def test_bitbucket_uses_configured_username(self) -> None:
        result = git_http_username(
            _repo(provider='bitbucket',
                  **{BITBUCKET_USERNAME_ATTR: 'configured-user'}),
            'https://bitbucket.org/org/repo.git',
        )
        self.assertEqual(result, 'configured-user')

    def test_bitbucket_falls_back_to_url_username(self) -> None:
        # URL has ``user@host`` embedded.
        result = git_http_username(
            _repo(provider='bitbucket'),
            'https://shay@bitbucket.org/org/repo.git',
        )
        self.assertEqual(result, 'shay')

    def test_bitbucket_falls_back_to_x_token_auth(self) -> None:
        result = git_http_username(
            _repo(provider='bitbucket'),
            'https://bitbucket.org/org/repo.git',
        )
        self.assertEqual(result, 'x-token-auth')

    def test_non_bitbucket_returns_parsed_username_when_present(self) -> None:
        # Line 63: ``return parsed.username`` for non-bitbucket providers
        # when the URL has an embedded ``user@host``.
        result = git_http_username(
            _repo(provider='github'),
            'https://octocat@github.com/org/repo.git',
        )
        self.assertEqual(result, 'octocat')

    def test_github_default_username_when_no_url_username(self) -> None:
        result = git_http_username(
            _repo(provider='github'),
            'https://github.com/org/repo.git',
        )
        self.assertEqual(result, 'x-access-token')

    def test_gitlab_default_username(self) -> None:
        result = git_http_username(
            _repo(provider='gitlab'),
            'https://gitlab.com/org/repo.git',
        )
        self.assertEqual(result, 'oauth2')

    def test_unknown_provider_falls_back_to_git(self) -> None:
        result = git_http_username(
            _repo(provider='azure'),
            'https://dev.azure.com/org/repo.git',
        )
        self.assertEqual(result, 'git')


if __name__ == '__main__':
    unittest.main()
