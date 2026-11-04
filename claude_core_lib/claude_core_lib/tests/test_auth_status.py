"""``claude_login_status`` against fake ``claude`` executables."""

from __future__ import annotations

import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_core_lib.claude_core_lib.helpers import auth_status
from claude_core_lib.claude_core_lib.helpers.auth_status import claude_login_status


@unittest.skipIf(sys.platform == 'win32', 'fake executables are POSIX scripts')
class LoginStatusTests(unittest.TestCase):
    def _binary(self, body: str) -> str:
        folder = Path(tempfile.mkdtemp(prefix='fake-claude-'))
        self.addCleanup(shutil.rmtree, folder, True)
        path = folder / 'claude'
        path.write_text('#!/bin/sh\n' + body, encoding='utf-8')
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return str(path)

    def test_signed_in(self) -> None:
        binary = self._binary(
            'echo \'{"loggedIn": true, "authMethod": "claude.ai", "email": "someone@example.com"}\'\n',
        )
        self.assertEqual(claude_login_status(binary), {'logged_in': True, 'method': 'claude.ai'})

    def test_signed_out_exits_non_zero_and_still_answers(self) -> None:
        binary = self._binary('echo \'{"loggedIn": false, "authMethod": "none"}\'\nexit 1\n')
        self.assertEqual(claude_login_status(binary), {'logged_in': False, 'method': 'none'})

    def test_it_asks_with_no_terminal_and_the_right_command(self) -> None:
        binary = self._binary('[ -t 0 ] && echo tty; echo "$@" > "$0.args"; echo \'{"loggedIn": true}\'\n')
        self.assertEqual(claude_login_status(binary), {'logged_in': True, 'method': ''})
        self.assertEqual(Path(binary + '.args').read_text().strip(), 'auth status --json')

    def test_an_answer_it_cannot_read_is_cannot_tell(self) -> None:
        for body in ('echo "error: unknown command auth"; exit 1\n', 'echo \'["loggedIn"]\'\n',
                     'echo \'{"loggedIn": "yes"}\'\n'):
            self.assertEqual(claude_login_status(self._binary(body)), {'logged_in': None, 'method': ''})

    def test_no_cli_or_no_answer_in_time_is_cannot_tell(self) -> None:
        self.assertEqual(claude_login_status('/no/such/claude'), {'logged_in': None, 'method': ''})
        with patch.object(auth_status.subprocess, 'run', side_effect=auth_status.subprocess.TimeoutExpired('x', 1)) as run:
            self.assertEqual(claude_login_status('', timeout=1), {'logged_in': None, 'method': ''})
        self.assertEqual(run.call_args.args[0][0], 'claude')  # no binary named: the one on PATH


if __name__ == '__main__':
    unittest.main()
