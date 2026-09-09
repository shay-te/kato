"""A 401 from the git provider has to say what to change.

A bare ``401 Client Error: Unauthorized``, repeated once per repository on
every scan, tells the operator nothing they can act on — and the setting that
fixes it is not guessable. Bitbucket's own response body is:

    "API token must be used with an atlassian registered email"

Atlassian API tokens authenticate as EMAIL + token; the older app passwords
used USERNAME + password. kato prefers ``api_email`` and falls back to
``username``, so an operator who pastes a new API token without setting the
email gets a guaranteed 401 forever, with nothing naming the fix.
"""

from __future__ import annotations

import unittest

from kato_core_lib.data_layers.service.task_publish_service import _auth_hint_for


class ProviderAuthHintTests(unittest.TestCase):
    def test_names_the_setting_when_bitbucket_says_why(self) -> None:
        hint = _auth_hint_for(Exception(
            '401 Client Error: Unauthorized for url: '
            'https://api.bitbucket.org/2.0/repositories/x/y/pullrequests — '
            '{"error": {"message": "API token must be used with an atlassian '
            'registered email"}}'
        ))
        self.assertIn('BITBUCKET_API_EMAIL', hint)
        self.assertIn('EMAIL + token', hint)

    def test_a_bare_401_still_points_at_the_likely_cause(self) -> None:
        hint = _auth_hint_for(Exception('401 Client Error: Unauthorized for url: ...'))
        self.assertIn('BITBUCKET_API_EMAIL', hint)

    def test_other_failures_get_no_hint(self) -> None:
        # A rate limit or a network blip is not an auth problem, and pointing
        # the operator at their credentials for one would send them the wrong
        # way entirely.
        self.assertEqual(_auth_hint_for(Exception('429 Too Many Requests')), '')
        self.assertEqual(_auth_hint_for(Exception('Connection reset by peer')), '')
        self.assertEqual(_auth_hint_for(Exception('404 Not Found')), '')
        self.assertEqual(_auth_hint_for(None), '')


if __name__ == '__main__':
    unittest.main()
