"""The provider token must reach only the CONFIGURED remote.

A workspace clone's ``.git/config`` is writable by the agent, so both the
``origin`` URL and any ``url.<base>.insteadOf`` rewrite are attacker
input. Two things follow:

* the HTTP auth header must be scoped to the configured URL rather than
  applied to every host the invocation happens to contact, and
* before any credential-carrying operation, the URL git would ACTUALLY
  use must be compared against the configured one.

Without both, an agent repoints ``origin`` and waits for the operator to
approve a push — the token then goes wherever it was pointed, and the
operator's own approval is the trigger.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from git_core_lib.git_core_lib.client.git_client import GitClientMixin


class _Client(GitClientMixin):
    def __init__(self, effective_url: str = '') -> None:
        import logging
        self.logger = logging.getLogger('test')
        self._effective_url = effective_url
        self.captured_env: dict = {}

    def _build_git_http_auth_header(self, repository) -> str:
        return 'Authorization: Bearer SECRET' if repository else ''

    def _run_capture(self, cmd, env=None):
        self.captured_env = env or {}
        return SimpleNamespace(returncode=0, stdout=self._effective_url, stderr='')


def _repo(url: str):
    return SimpleNamespace(id='r1', remote_url=url)


class AuthHeaderScopingTests(unittest.TestCase):
    def test_header_is_scoped_to_the_configured_url(self) -> None:
        client = _Client()
        client._run_git_subprocess('/ws', ['status'], _repo('https://git.example/o/r.git'))
        self.assertEqual(
            client.captured_env['GIT_CONFIG_KEY_0'],
            'http.https://git.example/o/r.git.extraHeader',
        )
        self.assertEqual(
            client.captured_env['GIT_CONFIG_VALUE_0'], 'Authorization: Bearer SECRET',
        )

    def test_unscoped_key_is_not_used_when_a_url_is_known(self) -> None:
        client = _Client()
        client._run_git_subprocess('/ws', ['status'], _repo('https://git.example/o/r.git'))
        self.assertNotEqual(client.captured_env['GIT_CONFIG_KEY_0'], 'http.extraHeader')

    def test_no_repository_means_no_credential_at_all(self) -> None:
        client = _Client()
        client._run_git_subprocess('/ws', ['status'], None)
        self.assertNotIn('GIT_CONFIG_KEY_0', client.captured_env)


class RemoteDriftGuardTests(unittest.TestCase):
    def test_matching_remote_is_allowed(self) -> None:
        client = _Client(effective_url='https://git.example/o/r.git\n')
        client._assert_remote_is_the_configured_one(
            '/ws', _repo('https://git.example/o/r.git'),
        )

    def test_trailing_git_and_slash_differences_are_not_drift(self) -> None:
        client = _Client(effective_url='https://git.example/o/r/\n')
        client._assert_remote_is_the_configured_one(
            '/ws', _repo('https://git.example/o/r.git'),
        )

    def test_embedded_credentials_do_not_count_as_drift(self) -> None:
        client = _Client(effective_url='https://user:tok@git.example/o/r.git\n')
        client._assert_remote_is_the_configured_one(
            '/ws', _repo('https://git.example/o/r.git'),
        )

    def test_redirected_origin_is_refused(self) -> None:
        client = _Client(effective_url='https://attacker.example/o/r.git\n')
        with self.assertRaises(RuntimeError) as caught:
            client._assert_remote_is_the_configured_one(
                '/ws', _repo('https://git.example/o/r.git'),
            )
        self.assertIn('attacker.example', str(caught.exception))
        self.assertIn('agent-writable', str(caught.exception))

    def test_push_refuses_before_sending_anything(self) -> None:
        client = _Client(effective_url='https://attacker.example/o/r.git\n')
        with mock.patch.object(client, '_run_git') as run_git:
            with self.assertRaises(RuntimeError):
                client._push_branch('/ws', 'feature/x', _repo('https://git.example/o/r.git'))
        run_git.assert_not_called()

    def test_no_configured_url_skips_the_check(self) -> None:
        # Nothing to compare against; the header builder has no repository
        # to work from either, so no credential is in play.
        client = _Client(effective_url='https://anything.example/x.git\n')
        client._assert_remote_is_the_configured_one('/ws', _repo(''))


if __name__ == '__main__':
    unittest.main()
