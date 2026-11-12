"""Strict or local git: what a Claude spawn refuses, and what it tells the agent.

The level is decided ONCE per session (``StreamingClaudeSession.git_access``)
and both the CLI deny list and the system prompt read it, so the agent is
never told it may run something the CLI then refuses — or the reverse.
Driven through the real deny-list merge, the real streaming command and the
real manager; only the subprocess is never started.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import WORKSPACES_ROOT_ENV
from agent_core_lib.agent_core_lib.helpers.command_floor import (
    GIT_LOCAL_HISTORY_SUBCOMMANDS,
)
from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
from claude_core_lib.claude_core_lib.session.streaming import StreamingClaudeSession

_LOCAL_PROMPT_MARK = 'GIT ACCESS FOR THIS SESSION: LOCAL'
_STAYS_DENIED = ('push', 'pull', 'fetch', 'clone', 'remote', 'config',
                 'checkout', 'switch', 'branch', 'bisect', 'update-ref')


def _items(**kwargs) -> list[str]:
    return ClaudeCliClient._merge_disallowed_with_floor('', **kwargs).split(',')


class DenyListTests(unittest.TestCase):
    def test_strict_is_the_default_and_denies_the_history_commands(self) -> None:
        self.assertEqual(_items(), _items(git_access='strict'))
        for sub in GIT_LOCAL_HISTORY_SUBCOMMANDS:
            self.assertIn(f'Bash(git {sub}:*)', _items(), sub)

    def test_local_releases_exactly_the_history_commands(self) -> None:
        items = _items(git_access='local')
        for sub in GIT_LOCAL_HISTORY_SUBCOMMANDS:
            with self.subTest(sub=sub):
                self.assertNotIn(f'Bash(git {sub}:*)', items)
                self.assertNotIn(f'Bash(git {sub} *)', items)
        for sub in _STAYS_DENIED:
            with self.subTest(sub=sub):
                self.assertIn(f'Bash(git {sub}:*)', items)

    def test_local_keeps_the_program_floor(self) -> None:
        for pattern in ClaudeCliClient.ACTION_GUARD_DENY_PATTERNS:
            self.assertIn(pattern, _items(git_access='local'))

    def test_bypass_withdraws_local_back_to_strict(self) -> None:
        self.assertEqual(
            set(_items(bypass_permissions=True, git_access='local')),
            set(_items(bypass_permissions=True)),
        )

    def test_an_unknown_level_is_strict(self) -> None:
        self.assertEqual(set(_items(git_access='full')), set(_items()))

    def test_the_operator_list_still_cannot_remove_the_floor(self) -> None:
        merged = ClaudeCliClient._merge_disallowed_with_floor(
            'Bash(rm:*)', git_access='local',
        ).split(',')
        self.assertIn('Bash(rm:*)', merged)
        self.assertIn('Bash(git push:*)', merged)


def _task_repo(test: unittest.TestCase) -> tuple[str, str]:
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    root = os.path.realpath(tmp.name)
    repo = os.path.join(root, 'PROJ-1', 'api')
    os.makedirs(repo)
    return root, repo


class StreamingSessionTests(unittest.TestCase):
    def _command(self, **kwargs) -> tuple[StreamingClaudeSession, list[str]]:
        root, repo = _task_repo(self)
        session = StreamingClaudeSession(task_id='PROJ-1', cwd=repo, **kwargs)
        with patch.dict(os.environ, {WORKSPACES_ROOT_ENV: root}), patch(
            'shutil.which', return_value='/usr/local/bin/claude',
        ):
            return session, session._build_command()

    @staticmethod
    def _flag(command: list[str], flag: str) -> str:
        return command[command.index(flag) + 1]

    def test_no_level_given_is_strict_and_says_nothing_extra(self) -> None:
        session, command = self._command()
        self.assertEqual(session.git_access, 'strict')
        self.assertIn('Bash(git commit:*)', self._flag(command, '--disallowedTools'))
        self.assertNotIn(_LOCAL_PROMPT_MARK, self._flag(command, '--append-system-prompt'))

    def test_local_releases_commit_and_tells_the_agent(self) -> None:
        session, command = self._command(git_access='local')
        self.assertEqual(session.git_access, 'local')
        denied = self._flag(command, '--disallowedTools').split(',')
        self.assertNotIn('Bash(git commit:*)', denied)
        self.assertIn('Bash(git push:*)', denied)
        prompt = self._flag(command, '--append-system-prompt')
        self.assertIn(_LOCAL_PROMPT_MARK, prompt)
        self.assertIn('`git commit`', prompt)

    def test_the_host_guidance_still_has_the_last_word(self) -> None:
        _session, command = self._command(git_access='local', extra_system_prompt='# Mine')
        prompt = self._flag(command, '--append-system-prompt')
        self.assertTrue(prompt.endswith('# Mine'), prompt[-80:])
        self.assertLess(prompt.index(_LOCAL_PROMPT_MARK), prompt.index('# Mine'))

    def test_auto_mode_gets_strict_in_both_places(self) -> None:
        session, command = self._command(
            git_access='local', permission_mode='bypassPermissions',
        )
        self.assertEqual(session.git_access, 'strict')
        denied = self._flag(command, '--disallowedTools').split(',')
        self.assertIn('Bash(git commit:*)', denied)
        self.assertIn('Bash(git restore:*)', denied)
        self.assertNotIn(_LOCAL_PROMPT_MARK, self._flag(command, '--append-system-prompt'))

    def test_the_recorded_deny_list_matches_what_a_fresh_merge_expects(self) -> None:
        # The host compares ``disallowed_tools``-at-spawn against a fresh merge
        # to decide a respawn; at the same level the two must be equal.
        _session, command = self._command(git_access='local')
        self.assertEqual(
            set(self._flag(command, '--disallowedTools').split(',')),
            set(_items(git_access='local')),
        )


class ManagerTests(unittest.TestCase):
    def _made(self, **start_kwargs) -> dict:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        made: list[dict] = []

        class _Fake:
            def __init__(self, **kwargs) -> None:
                made.append(kwargs)
                self.task_id = kwargs['task_id']

            cwd = property(lambda self: '')
            agent_session_id = property(lambda self: 'sess-1')
            is_alive = property(lambda self: True)

            def start(self, initial_prompt: str = '') -> None:
                pass

        manager = ClaudeSessionManager(state_dir=Path(tmp.name), session_factory=_Fake)
        manager.start_session(task_id='PROJ-9', **start_kwargs)
        return made[-1]

    def test_the_manager_hands_the_level_to_the_session(self) -> None:
        self.assertEqual(self._made(git_access='local')['git_access'], 'local')

    def test_no_level_is_an_empty_string_the_session_reads_as_strict(self) -> None:
        self.assertEqual(self._made()['git_access'], '')


if __name__ == '__main__':
    unittest.main()
