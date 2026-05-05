"""Tests for ``_resolve_diff_base`` — the policy that picks which
``origin/<branch>`` the Changes tab diffs against.

Pinned because this is the single load-bearing rule that prevents
the regression where a repo with default ``master`` but a kato-
configured base of ``develop`` diffs against the wrong branch and
shows the operator hundreds of unrelated commits.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from kato_webserver import app as webserver_app


class _AgentServiceStub:
    def __init__(self, mapping):
        self._mapping = dict(mapping)

    def configured_destination_branch(self, repo_id):
        return self._mapping.get(repo_id, '')


class ResolveDiffBaseTests(unittest.TestCase):
    def test_configured_destination_branch_wins_over_git(self) -> None:
        # The whole point of this resolver: never let git
        # auto-detection override the kato-configured base. The
        # operator's config says ``develop`` — that's what we use,
        # even if the local clone advertises ``main`` as the
        # remote default.
        agent = _AgentServiceStub({'client': 'develop'})
        with patch.object(
            webserver_app, 'detect_default_branch', return_value='main',
        ) as detect:
            result = webserver_app._resolve_diff_base('client', '/cwd', agent)
        self.assertEqual(result, 'develop')
        # Crucially: we never even called git. Configured value short-circuits.
        detect.assert_not_called()

    def test_falls_back_to_git_detection_when_no_config_value(self) -> None:
        # Unknown repo id (or no inventory entry) returns '' from
        # the lookup → git detection takes over. The git detector
        # is itself authoritative about the *remote's* default;
        # see the dedicated test module.
        agent = _AgentServiceStub({})  # nothing configured
        with patch.object(
            webserver_app, 'detect_default_branch', return_value='trunk',
        ):
            result = webserver_app._resolve_diff_base('unknown', '/cwd', agent)
        self.assertEqual(result, 'trunk')

    def test_returns_empty_when_no_config_and_git_detection_fails(self) -> None:
        # Both sources empty → empty answer; the caller surfaces a
        # precise operator-facing error. This is the "fix your
        # config" case, NOT a "guess main and hope" case.
        agent = _AgentServiceStub({})
        with patch.object(
            webserver_app, 'detect_default_branch', return_value='',
        ):
            result = webserver_app._resolve_diff_base('repo', '/cwd', agent)
        self.assertEqual(result, '')

    def test_handles_missing_agent_service(self) -> None:
        # The webserver passes None when the agent service isn't
        # wired (test fixtures, half-set-up boots). Resolver must
        # tolerate it and fall through to git detection rather
        # than raise.
        with patch.object(
            webserver_app, 'detect_default_branch', return_value='main',
        ):
            result = webserver_app._resolve_diff_base('client', '/cwd', None)
        self.assertEqual(result, 'main')

    def test_handles_agent_service_without_lookup_method(self) -> None:
        # Defensive against an older agent service (e.g. during a
        # rolling upgrade where the webserver lands first). No
        # lookup method → fall through to git, don't raise.
        class _Old:
            pass
        with patch.object(
            webserver_app, 'detect_default_branch', return_value='main',
        ):
            result = webserver_app._resolve_diff_base('client', '/cwd', _Old())
        self.assertEqual(result, 'main')

    def test_no_base_error_message_names_the_repo_and_points_at_the_config(self) -> None:
        msg = webserver_app._no_base_error_message('client')
        self.assertIn("'client'", msg)
        self.assertIn('destination_branch', msg)
        self.assertIn('kato config', msg)


if __name__ == '__main__':
    unittest.main()
