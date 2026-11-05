"""Whose agent finished a task: the name kato signs its Slack posts with.

Several developers' katos post "UNA-3267 is done" to one channel, and the post
did not say whose agent it was. The name is the operator's own (Settings), else
their git user.name — read from a real global git config in a throwaway HOME —
else the computer's name.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.helpers.agent_name_utils import (
    AGENT_NAME_KEY,
    AGENT_NAME_MAX_CHARS,
    agent_name,
    global_git_user_name,
)


class AgentNameTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='agent-name-')
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.settings = self.home / 'settings.json'
        self.settings.write_text('{}', encoding='utf-8')
        env = {key: value for key, value in os.environ.items()
               if key not in ('GIT_CONFIG_GLOBAL', 'GIT_CONFIG_SYSTEM', 'XDG_CONFIG_HOME', AGENT_NAME_KEY)}
        env.update({'HOME': str(self.home), 'USERPROFILE': str(self.home),
                    'GIT_CONFIG_NOSYSTEM': '1', 'KATO_SETTINGS_FILE': str(self.settings)})
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def git_user(self, name: str) -> None:
        subprocess.run(['git', 'config', '--global', 'user.name', name],
                       check=True, capture_output=True, stdin=subprocess.DEVNULL)

    def test_the_developer_s_git_name_by_default(self) -> None:
        self.git_user('Dana Levi')
        self.assertEqual(global_git_user_name(), 'Dana Levi')
        self.assertEqual(agent_name(), "Dana Levi's kato")

    def test_the_name_typed_in_settings_wins_and_a_save_applies_at_once(self) -> None:
        self.git_user('Dana Levi')
        self.settings.write_text(json.dumps({AGENT_NAME_KEY: '  Dana   (laptop) '}), encoding='utf-8')
        self.assertEqual(agent_name(), 'Dana (laptop)')
        self.settings.write_text('{}', encoding='utf-8')
        self.assertEqual(agent_name(), "Dana Levi's kato")

    def test_an_email_in_git_s_user_name_is_never_posted(self) -> None:
        # This machine's own case: git's user.name was an email address.
        self.git_user('dana.levi@example.com')
        self.assertEqual(agent_name(account_name=lambda: 'Dana Levi'), "Dana Levi's kato")
        self.assertEqual(agent_name(account_name=lambda: ''), "dana.levi's kato")

    @unittest.skipIf(sys.platform == 'win32', 'Windows keeps no account full name (no pwd)')
    def test_the_account_s_full_name_drops_the_room_and_phone_fields(self) -> None:
        from kato_core_lib.helpers import agent_name_utils
        fake = type('pw', (), {'pw_gecos': 'Dana  Levi,Room 4,555-1234'})()
        with patch('pwd.getpwuid', return_value=fake):
            self.assertEqual(agent_name_utils.os_account_full_name(), 'Dana Levi')
        with patch('pwd.getpwuid', side_effect=KeyError('no such user')):
            self.assertEqual(agent_name_utils.os_account_full_name(), '')

    def test_no_git_name_takes_the_account_s_full_name(self) -> None:
        self.assertEqual(global_git_user_name(), '')
        self.assertEqual(agent_name(account_name=lambda: 'Dana Levi'), "Dana Levi's kato")

    def test_no_name_at_all_falls_back_to_the_computer(self) -> None:
        no_one = lambda: ''  # noqa: E731
        self.assertEqual(agent_name(account_name=no_one, host_name=lambda: 'Danas-MacBook'),
                         'kato on Danas-MacBook')
        self.assertEqual(agent_name(account_name=no_one, host_name=lambda: ''), 'kato')

    def test_a_git_that_cannot_run_says_nothing(self) -> None:
        with patch('kato_core_lib.helpers.agent_name_utils.subprocess.run', side_effect=OSError('no git')):
            self.assertEqual(global_git_user_name(), '')

    def test_a_very_long_name_is_kept_short(self) -> None:
        self.settings.write_text(json.dumps({AGENT_NAME_KEY: 'x' * 500}), encoding='utf-8')
        self.assertEqual(len(agent_name()), AGENT_NAME_MAX_CHARS)


if __name__ == '__main__':
    unittest.main()
