"""Coverage for ``repository_core_lib/helpers/provider_cli_utils.py``.

The probes reuse a developer's existing ``gh`` / ``glab`` login instead
of asking them to mint a token. Like the git-credential helper, every
failure is silent (CLI absent, signed out, wedged) because the caller
just falls through to the next source — so these tests pin the silence
as much as the happy path.
"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from repository_core_lib.repository_core_lib.helpers.provider_cli_utils import (
    provider_cli_binary,
    provider_cli_installed,
    read_provider_cli_account,
    read_provider_cli_token,
)


MODULE = 'repository_core_lib.repository_core_lib.helpers.provider_cli_utils'


def _completed(stdout: str = '', stderr: str = '', returncode: int = 0):
    return subprocess.CompletedProcess(
        args=['gh'], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class ProviderCliBinaryTests(unittest.TestCase):
    def test_known_providers(self) -> None:
        self.assertEqual(provider_cli_binary('github'), 'gh')
        self.assertEqual(provider_cli_binary('GitHub'), 'gh')
        self.assertEqual(provider_cli_binary('gitlab'), 'glab')

    def test_providers_without_a_cli(self) -> None:
        for name in ('bitbucket', 'jira', 'youtrack', '', None):
            self.assertEqual(provider_cli_binary(name), '')

    def test_installed_checks_path(self) -> None:
        with patch(f'{MODULE}.shutil.which', return_value='/usr/bin/gh'):
            self.assertTrue(provider_cli_installed('github'))
        with patch(f'{MODULE}.shutil.which', return_value=None):
            self.assertFalse(provider_cli_installed('github'))
        # No CLI for the provider at all → never consults PATH.
        with patch(f'{MODULE}.shutil.which') as which:
            self.assertFalse(provider_cli_installed('jira'))
        which.assert_not_called()


class ReadProviderCliTokenTests(unittest.TestCase):
    def test_returns_the_token(self) -> None:
        with patch(f'{MODULE}.shutil.which', return_value='/usr/bin/gh'), \
             patch(f'{MODULE}.subprocess.run', return_value=_completed('ghp_live\n')) as run:
            self.assertEqual(read_provider_cli_token('github'), 'ghp_live')
        self.assertEqual(run.call_args.args[0], ['gh', 'auth', 'token'])

    def test_hostname_targets_a_self_hosted_instance(self) -> None:
        with patch(f'{MODULE}.shutil.which', return_value='/usr/bin/glab'), \
             patch(f'{MODULE}.subprocess.run', return_value=_completed('t\n')) as run:
            read_provider_cli_token('gitlab', 'git.acme.io')
        self.assertEqual(
            run.call_args.args[0], ['glab', 'auth', 'token', '--hostname', 'git.acme.io'],
        )

    def test_cli_absent_never_shells_out(self) -> None:
        with patch(f'{MODULE}.shutil.which', return_value=None), \
             patch(f'{MODULE}.subprocess.run') as run:
            self.assertEqual(read_provider_cli_token('github'), '')
        run.assert_not_called()

    def test_signed_out_returns_empty(self) -> None:
        with patch(f'{MODULE}.shutil.which', return_value='/usr/bin/gh'), \
             patch(f'{MODULE}.subprocess.run', return_value=_completed('', 'no token', 1)):
            self.assertEqual(read_provider_cli_token('github'), '')

    def test_wedged_cli_times_out_quietly(self) -> None:
        with patch(f'{MODULE}.shutil.which', return_value='/usr/bin/gh'), \
             patch(f'{MODULE}.subprocess.run',
                   side_effect=subprocess.TimeoutExpired(cmd='gh', timeout=15)):
            self.assertEqual(read_provider_cli_token('github'), '')

    def test_provider_without_a_cli(self) -> None:
        self.assertEqual(read_provider_cli_token('jira'), '')


class ReadProviderCliAccountTests(unittest.TestCase):
    def test_parses_the_modern_account_phrasing(self) -> None:
        with patch(f'{MODULE}.shutil.which', return_value='/usr/bin/gh'), \
             patch(f'{MODULE}.subprocess.run',
                   return_value=_completed('✓ Logged in to github.com account octocat (keyring)')):
            self.assertEqual(read_provider_cli_account('github'), 'octocat')

    def test_parses_the_older_as_phrasing_on_stderr(self) -> None:
        with patch(f'{MODULE}.shutil.which', return_value='/usr/bin/gh'), \
             patch(f'{MODULE}.subprocess.run',
                   return_value=_completed('', '✓ Logged in to github.com as octo-cat (oauth_token)')):
            self.assertEqual(read_provider_cli_account('github'), 'octo-cat')

    def test_unparseable_output_is_not_a_failure_signal(self) -> None:
        # Only the TOKEN answers "signed in?" — a missing name is cosmetic.
        with patch(f'{MODULE}.shutil.which', return_value='/usr/bin/gh'), \
             patch(f'{MODULE}.subprocess.run', return_value=_completed('something new')):
            self.assertEqual(read_provider_cli_account('github'), '')

    def test_cli_absent_or_crashed(self) -> None:
        with patch(f'{MODULE}.shutil.which', return_value=None):
            self.assertEqual(read_provider_cli_account('github'), '')
        with patch(f'{MODULE}.shutil.which', return_value='/usr/bin/gh'), \
             patch(f'{MODULE}.subprocess.run', side_effect=OSError('boom')):
            self.assertEqual(read_provider_cli_account('github'), '')


if __name__ == '__main__':
    unittest.main()
