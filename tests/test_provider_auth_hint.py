"""A git host's refusal has to say what to change — ONE place says it.

A bare ``401`` / ``403``, repeated once per repository on every scan, tells
the operator nothing they can act on, and the fix is not guessable:

* Bitbucket's own body: "API token must be used with an atlassian registered
  email" — Atlassian API tokens sign in as EMAIL + token.
* 2026-11-15, "what token permissions is missing?": a Bitbucket API token
  with repository scopes only passed every connection check (they read the
  repository) and got 403 on every pull request lookup.

``refusal_hint`` (helpers/git_host_refusal_utils.py) is the one answer; the
review scan's log and banner, Test connection and the pull request step use it.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from kato_core_lib.data_layers.service.connection_health import ConnectionHealth
from kato_core_lib.helpers.git_host_refusal_utils import BITBUCKET_TOKEN_SCOPES, refusal_hint

REPO = Path(__file__).resolve().parents[1]
BITBUCKET_403 = (
    '403 Client Error: Forbidden for url: '
    'https://api.bitbucket.org/2.0/repositories/shacoshe/ob-love-admin-admin-client/pullrequests?pagelen=50'
)


class RefusalHintTests(unittest.TestCase):
    def test_a_bitbucket_403_names_every_scope_and_where_to_paste_the_new_token(self) -> None:
        hint = refusal_hint(Exception(BITBUCKET_403))
        for scope in ('read:pullrequest:bitbucket', 'write:pullrequest:bitbucket',
                      'read:repository:bitbucket', 'write:repository:bitbucket'):
            self.assertIn(scope, hint)
        self.assertEqual(len(BITBUCKET_TOKEN_SCOPES), 4)
        self.assertIn('cannot be added to a token', hint)
        self.assertIn('Settings → Git provider → Bitbucket', hint)

    def test_bitbucket_saying_why_names_the_email_setting(self) -> None:
        hint = refusal_hint(Exception(
            '401 Client Error: Unauthorized for url: https://api.bitbucket.org/2.0/repositories/x/y/pullrequests'
            ' — {"error": {"message": "API token must be used with an atlassian registered email"}}'
        ))
        self.assertIn('EMAIL + token', hint)
        self.assertIn('API email', hint)
        self.assertIn('Settings → Git provider → Bitbucket', hint)

    def test_a_bare_bitbucket_401_points_at_the_token_and_the_email(self) -> None:
        hint = refusal_hint(Exception('401 Client Error: Unauthorized for url: https://api.bitbucket.org/2.0/x'))
        self.assertIn('API email', hint)

    def test_github_refusals_name_the_grants(self) -> None:
        for code, words in (('404', 'cannot WRITE'), ('403', 'missing a permission'), ('401', 'rejected the token')):
            with self.subTest(code=code):
                hint = refusal_hint(Exception(f'{code} Client Error: for url: https://api.github.com/repos/a/b/pulls'))
                self.assertIn(words, hint)
                self.assertIn('Settings → Git provider → GitHub', hint)
                self.assertNotIn('BITBUCKET', hint.upper().replace('GITHUB', ''))

    def test_gitlab_refusals_name_the_api_scope(self) -> None:
        self.assertIn('api scope', refusal_hint(Exception('403 Forbidden for url: https://gitlab.com/api/v4/x')))
        self.assertIn('rejected', refusal_hint(Exception('401 Unauthorized for url: https://gitlab.com/api/v4/x')))
        self.assertEqual(refusal_hint(Exception('500 for url: https://gitlab.com/api/v4/x')), '')

    def test_an_azure_refusal_names_the_azure_token(self) -> None:
        for text in (
            '401 Client Error: Unauthorized for url: https://dev.azure.com/acme/proj/_apis/git/...',
            '401 Client Error: Unauthorized: TF400813: The user is not authorized to access this resource.',
        ):
            with self.subTest(text=text):
                hint = refusal_hint(Exception(text))
                self.assertIn('Code (Read & Write)', hint)
                self.assertIn('Settings → Git provider → Azure DevOps', hint)

    def test_an_unknown_host_401_still_points_at_settings(self) -> None:
        self.assertIn('Settings → Git provider', refusal_hint(Exception('401 Unauthorized for url: https://git.acme/x')))

    def test_other_failures_get_no_hint(self) -> None:
        # A rate limit or a network blip is not a refusal.
        for error in ('429 Too Many Requests', 'Connection reset by peer', '404 Not Found', None,
                      '500 Server Error for url: https://api.bitbucket.org/2.0/x',
                      '502 for url: https://api.github.com/x'):
            with self.subTest(error=error):
                self.assertEqual(refusal_hint(Exception(error) if error else None), '')

    def test_never_an_env_var_name(self) -> None:
        for text in (BITBUCKET_403, '403 for url: https://api.github.com/x', '403 https://gitlab.com/x',
                     '401 https://dev.azure.com/x', 'atlassian registered email'):
            self.assertNotIn('_API_', refusal_hint(Exception(text)))


class EverySurfaceSaysItTests(unittest.TestCase):
    def test_the_banner_says_what_to_change(self) -> None:
        from kato_core_lib.data_layers.service.review_comment_service import ReviewCommentService

        health = ConnectionHealth()
        service = SimpleNamespace(_connection_health=health)
        ReviewCommentService._report_git_host(service, SimpleNamespace(provider='bitbucket'), Exception(BITBUCKET_403))
        [problem] = health.problems()
        self.assertIn('read:pullrequest:bitbucket', str(problem))
        ReviewCommentService._report_git_host(service, SimpleNamespace(provider='bitbucket'))
        self.assertEqual(health.problems(), [])  # it answered: the banner goes

    def test_test_connection_says_what_to_change(self) -> None:
        from kato_core_lib.validation import git_host_connection_check as check

        class Refusing(object):
            def _prepare_pull_request_api(self, repository):
                pass

            def _pull_request_data_access(self, repository):
                return SimpleNamespace(validate_connection=lambda: (_ for _ in ()).throw(Exception(BITBUCKET_403)))

        answer = check._check_one(Refusing(), SimpleNamespace(id='admin-client'))
        self.assertFalse(answer['ok'])
        self.assertIn('write:pullrequest:bitbucket', answer['error'])

    def test_no_other_module_writes_its_own_hint(self) -> None:
        # Two copies of this drifted before (a 403 had no words in one of them).
        found = sorted(
            str(path.relative_to(REPO))
            for folder in ('kato_core_lib', 'webserver/kato_webserver')
            for path in (REPO / folder).rglob('*.py')
            if '/tests/' not in str(path)
            and any(words in path.read_text(encoding='utf-8', errors='replace')
                    for words in ('pullrequest:bitbucket', 'Contents: Read and write'))
        )
        self.assertEqual(found, ['kato_core_lib/helpers/git_host_refusal_utils.py'])


    def test_the_setup_help_lists_the_same_bitbucket_scopes(self) -> None:
        # The Settings help (credentialGuides.js) and the refusal must agree,
        # or the help makes the token the refusal then complains about.
        guide = (REPO / 'webserver' / 'ui' / 'src' / 'utils' / 'credentialGuides.js').read_text(encoding='utf-8')
        for scope in BITBUCKET_TOKEN_SCOPES:
            self.assertIn(scope, guide)

if __name__ == '__main__':
    unittest.main()
