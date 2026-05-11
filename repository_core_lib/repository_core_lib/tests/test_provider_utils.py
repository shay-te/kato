"""Coverage for ``repository_core_lib/helpers/provider_utils.py``.

These four helpers replaced the kato-side ``_fallback_web_base_url``,
``_provider_from_url_string``, ``_default_provider_base_url``, and
``_missing_pull_request_token_message`` static methods on
``RepositoryInventoryService``. The originals had no direct tests
because they were exercised only through kato integration paths; the
extracted helpers get their own unit tests here per CLAUDE.md core-lib
quality rule #1 (100% coverage).
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from repository_core_lib.repository_core_lib.helpers.provider_utils import (
    default_provider_base_url,
    fallback_web_base_url,
    missing_pull_request_token_message,
    provider_from_url_string,
)


class ProviderFromUrlStringTests(unittest.TestCase):
    """Maps a URL substring to a canonical provider name."""

    def test_detects_bitbucket(self) -> None:
        self.assertEqual(provider_from_url_string('https://bitbucket.org/org/repo'), 'bitbucket')
        self.assertEqual(provider_from_url_string('https://api.bitbucket.org/2.0'), 'bitbucket')

    def test_detects_github(self) -> None:
        self.assertEqual(provider_from_url_string('https://github.com/org/repo'), 'github')
        self.assertEqual(provider_from_url_string('https://api.github.com'), 'github')

    def test_detects_gitlab(self) -> None:
        self.assertEqual(provider_from_url_string('https://gitlab.com/org/repo'), 'gitlab')

    def test_case_insensitive_match(self) -> None:
        self.assertEqual(provider_from_url_string('https://GITHUB.com'), 'github')

    def test_returns_empty_for_unknown_provider(self) -> None:
        self.assertEqual(provider_from_url_string('https://azure.com/repo'), '')
        self.assertEqual(provider_from_url_string(''), '')

    def test_first_match_wins_when_multiple_keywords_appear(self) -> None:
        # Bitbucket is checked first — make sure that's stable.
        self.assertEqual(
            provider_from_url_string('https://example.com/bitbucket-mirror-of-github'),
            'bitbucket',
        )


class DefaultProviderBaseUrlTests(unittest.TestCase):
    def test_github_com_returns_canonical_api(self) -> None:
        # Public GitHub uses ``api.github.com``, not ``github.com/api/v3``.
        self.assertEqual(
            default_provider_base_url('github', 'git@github.com:org/repo.git'),
            'https://api.github.com',
        )

    def test_github_enterprise_uses_api_v3_path(self) -> None:
        # Self-hosted GitHub Enterprise: API lives at ``/api/v3``.
        self.assertEqual(
            default_provider_base_url('github', 'https://ghe.example.com/org/repo.git'),
            'https://ghe.example.com/api/v3',
        )

    def test_gitlab_uses_api_v4_path(self) -> None:
        self.assertEqual(
            default_provider_base_url('gitlab', 'https://gitlab.example.com/org/repo.git'),
            'https://gitlab.example.com/api/v4',
        )

    def test_bitbucket_cloud_returns_canonical_api(self) -> None:
        self.assertEqual(
            default_provider_base_url(
                'bitbucket', 'https://bitbucket.org/org/repo.git',
            ),
            'https://api.bitbucket.org/2.0',
        )

    def test_bitbucket_self_hosted_returns_empty(self) -> None:
        # Only bitbucket.org cloud has a known canonical API base.
        # Self-hosted Bitbucket Server (Stash) has a different layout
        # that the operator must configure explicitly.
        self.assertEqual(
            default_provider_base_url(
                'bitbucket', 'https://bitbucket.internal.corp/org/repo.git',
            ),
            '',
        )

    def test_returns_empty_when_remote_url_has_no_web_base(self) -> None:
        self.assertEqual(default_provider_base_url('github', ''), '')

    def test_returns_empty_for_unknown_provider(self) -> None:
        self.assertEqual(
            default_provider_base_url('azure', 'https://azure.com/r/r'),
            '',
        )


class FallbackWebBaseUrlTests(unittest.TestCase):
    """Compute web base URL from a repository, trying remote_url first."""

    def _repo(self, **kwargs):
        defaults = {'remote_url': '', 'provider_base_url': ''}
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_uses_remote_url_when_set(self) -> None:
        web_base = fallback_web_base_url(
            self._repo(remote_url='git@github.com:org/repo.git'),
        )
        self.assertTrue(web_base.startswith('https://'))
        self.assertIn('github.com', web_base)

    def test_returns_empty_when_no_remote_or_provider_url(self) -> None:
        self.assertEqual(fallback_web_base_url(self._repo()), '')

    def test_recognizes_api_bitbucket_org_provider_base_url(self) -> None:
        # ``api.bitbucket.org/2.0`` → web base is ``bitbucket.org``.
        self.assertEqual(
            fallback_web_base_url(self._repo(
                provider_base_url='https://api.bitbucket.org/2.0',
            )),
            'https://bitbucket.org',
        )

    def test_strips_api_v4_suffix_from_provider_url(self) -> None:
        self.assertEqual(
            fallback_web_base_url(self._repo(
                provider_base_url='https://gitlab.example.com/api/v4',
            )),
            'https://gitlab.example.com',
        )

    def test_strips_api_v3_suffix_from_provider_url(self) -> None:
        # GitHub Enterprise.
        self.assertEqual(
            fallback_web_base_url(self._repo(
                provider_base_url='https://ghe.example.com/api/v3',
            )),
            'https://ghe.example.com',
        )

    def test_strips_plain_api_suffix_from_provider_url(self) -> None:
        # Some self-hosted providers use bare ``/api`` (no version).
        self.assertEqual(
            fallback_web_base_url(self._repo(
                provider_base_url='https://example.com/api',
            )),
            'https://example.com',
        )

    def test_strips_handles_trailing_slash(self) -> None:
        # ``rstrip('/')`` normalizes the suffix check but the actual slice
        # preserves the trailing slash on the kept prefix. That's fine —
        # callers treat the result as a base URL.
        self.assertEqual(
            fallback_web_base_url(self._repo(
                provider_base_url='https://gitlab.example.com/api/v4/',
            )),
            'https://gitlab.example.com/',
        )

    def test_returns_provider_base_url_as_is_when_no_suffix_matches(self) -> None:
        # Some providers don't have a recognized API suffix; we hand it back
        # unchanged so the caller can decide what to do.
        self.assertEqual(
            fallback_web_base_url(self._repo(
                provider_base_url='https://random.example.com/path',
            )),
            'https://random.example.com/path',
        )


class MissingPullRequestTokenMessageTests(unittest.TestCase):
    """The error message that surfaces in startup logs when a token is missing."""

    def test_github_message_mentions_github_api_token_env_var(self) -> None:
        msg = missing_pull_request_token_message('client', 'github')
        self.assertIn('client', msg)
        self.assertIn('GITHUB_API_TOKEN', msg)

    def test_gitlab_message_mentions_gitlab_api_token_env_var(self) -> None:
        msg = missing_pull_request_token_message('client', 'gitlab')
        self.assertIn('GITLAB_API_TOKEN', msg)

    def test_bitbucket_message_mentions_bitbucket_api_token_env_var(self) -> None:
        msg = missing_pull_request_token_message('client', 'bitbucket')
        self.assertIn('BITBUCKET_API_TOKEN', msg)

    def test_unknown_provider_falls_back_to_placeholder(self) -> None:
        msg = missing_pull_request_token_message('client', 'azure')
        self.assertIn('<provider-token>', msg)


if __name__ == '__main__':
    unittest.main()
