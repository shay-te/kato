"""Coverage for ``git_core_lib/helpers/git_credential_utils.py``.

The helper asks git's own credential store for a host's saved secret so
a caller can reuse it instead of making a human mint a token. Every
failure mode here is a SILENT one by design (nothing stored, no helper,
git missing, helper wedged) — a caller just falls through to the next
credential source — so the tests pin that none of them raise, and that
the hardening flags survive.
"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from git_core_lib.git_core_lib.helpers.git_credential_utils import (
    parse_credential_output,
    read_git_credential,
)


def _completed(stdout: str = '', returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=['git'], returncode=returncode, stdout=stdout, stderr='',
    )


class ParseCredentialOutputTests(unittest.TestCase):
    def test_reads_username_and_password(self) -> None:
        self.assertEqual(
            parse_credential_output(
                'protocol=https\nhost=github.com\nusername=octocat\npassword=ghp_x\n',
            ),
            ('octocat', 'ghp_x'),
        )

    def test_missing_password_yields_empty_secret(self) -> None:
        self.assertEqual(
            parse_credential_output('username=octocat\n'), ('octocat', ''),
        )

    def test_tolerates_blank_and_malformed_lines(self) -> None:
        self.assertEqual(
            parse_credential_output('\ngarbage\npassword=s3cret\n'), ('', 's3cret'),
        )

    def test_empty_and_none_input(self) -> None:
        self.assertEqual(parse_credential_output(''), ('', ''))
        self.assertEqual(parse_credential_output(None), ('', ''))


class ReadGitCredentialTests(unittest.TestCase):
    def test_returns_the_stored_credential(self) -> None:
        with patch('git_core_lib.git_core_lib.helpers.git_credential_utils.subprocess.run',
                   return_value=_completed('username=octocat\npassword=ghp_x\n')):
            self.assertEqual(read_git_credential('github.com'), ('octocat', 'ghp_x'))

    def test_blank_host_never_shells_out(self) -> None:
        with patch('git_core_lib.git_core_lib.helpers.git_credential_utils.subprocess.run') as run:
            self.assertEqual(read_git_credential('  '), ('', ''))
        run.assert_not_called()

    def test_non_zero_exit_is_no_credential(self) -> None:
        with patch('git_core_lib.git_core_lib.helpers.git_credential_utils.subprocess.run',
                   return_value=_completed('', returncode=128)):
            self.assertEqual(read_git_credential('github.com'), ('', ''))

    def test_git_missing_is_no_credential(self) -> None:
        with patch('git_core_lib.git_core_lib.helpers.git_credential_utils.subprocess.run',
                   side_effect=FileNotFoundError('git')):
            self.assertEqual(read_git_credential('github.com'), ('', ''))

    def test_wedged_helper_times_out_quietly(self) -> None:
        with patch('git_core_lib.git_core_lib.helpers.git_credential_utils.subprocess.run',
                   side_effect=subprocess.TimeoutExpired(cmd='git', timeout=10)):
            self.assertEqual(read_git_credential('github.com'), ('', ''))

    def test_command_is_hardened_and_non_interactive(self) -> None:
        with patch('git_core_lib.git_core_lib.helpers.git_credential_utils.subprocess.run',
                   return_value=_completed('password=s\n')) as run:
            read_git_credential('github.com', working_directory='/home/dev')
        command = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        # Built through build_safe_git_command → hooks disabled.
        self.assertEqual(command[0], 'git')
        self.assertIn('core.hooksPath=/dev/null', command)
        self.assertEqual(command[-2:], ['credential', 'fill'])
        # Run OUTSIDE any repository, so a repo-local credential.helper
        # (which a hostile clone can set) is not what answers.
        self.assertIn('/home/dev', command)
        # A helper that would prompt must fail, not hang a caller with no TTY.
        self.assertEqual(kwargs['env']['GIT_TERMINAL_PROMPT'], '0')
        self.assertEqual(kwargs['env']['GCM_INTERACTIVE'], 'never')
        self.assertTrue(kwargs['timeout'] > 0)
        self.assertIn('host=github.com', kwargs['input'])

    def test_protocol_is_configurable_and_defaults_to_https(self) -> None:
        with patch('git_core_lib.git_core_lib.helpers.git_credential_utils.subprocess.run',
                   return_value=_completed('password=s\n')) as run:
            read_git_credential('example.com')
            self.assertIn('protocol=https', run.call_args.kwargs['input'])
            read_git_credential('example.com', protocol='http')
            self.assertIn('protocol=http', run.call_args.kwargs['input'])


if __name__ == '__main__':
    unittest.main()
