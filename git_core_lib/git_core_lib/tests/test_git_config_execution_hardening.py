"""Repo-local git config must not execute commands on the HOST.

The agent has read-write access to its whole workspace clone, ``.git``
included, and the host application runs git against that same clone. Any git
config key that spawns a process is therefore attacker-controlled input
to a host process — a sandbox-to-host escape that needs no container
break-out at all.

This was demonstrated, not theorised: with only ``core.hooksPath``
neutralised, writing

    [core]
        fsmonitor = "<command>"

into ``.git/config`` ran ``<command>`` on the host the next time the caller
issued ``git status`` on that clone — a routine operation.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from git_core_lib.git_core_lib.helpers.git_command_utils import (
    _EXECUTION_CONFIG_OVERRIDES,
    build_safe_git_command,
)


class ExecutionConfigOverrideTests(unittest.TestCase):
    def _flag_values(self, key: str) -> list[str]:
        command = build_safe_git_command('/tmp/x', ['status'])
        return [
            token for index, token in enumerate(command)
            if index and command[index - 1] == '-c' and token.startswith(f'{key}=')
        ]

    def test_hooks_are_still_disabled(self) -> None:
        self.assertIn('core.hooksPath=/dev/null', build_safe_git_command('/r', []))

    def test_every_command_executing_key_is_overridden(self) -> None:
        # ``diff.external`` is deliberately absent: overriding it here to
        # an empty value makes git run an empty external differ and every
        # patch comes back blank. It is neutralised with ``--no-ext-diff``
        # at the call sites that generate patches instead.
        for key in (
            'core.fsmonitor', 'core.sshCommand', 'core.gitProxy',
            'core.pager', 'core.editor', 'sequence.editor', 'protocol.ext.allow',
            'uploadpack.packObjectsHook', 'core.alternateRefsCommand',
            'http.followRedirects',
        ):
            self.assertTrue(
                self._flag_values(key),
                f'{key} can execute a command from repo-local config and is '
                f'not overridden — this is a host RCE path from the sandbox',
            )

    def test_overrides_precede_the_repository_argument(self) -> None:
        # ``-c`` after ``-C`` would still work, but keeping every override
        # ahead of the subcommand keeps the audit read in one glance.
        command = build_safe_git_command('/r', ['status'])
        self.assertLess(
            max(i for i, t in enumerate(command) if t == '-c'),
            command.index('-C'),
        )


class RealGitExecutionTests(unittest.TestCase):
    """Run real git against a poisoned repo. Skipped without a git binary."""

    def setUp(self) -> None:
        if subprocess.run(
            ['git', '--version'], capture_output=True,
        ).returncode != 0:                              # pragma: no cover
            self.skipTest('git unavailable')
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / 'repo'
        self.repo.mkdir()
        for args in (
            ['init', '-q'], ['config', 'user.email', 't@t'],
            ['config', 'user.name', 't'],
        ):
            subprocess.run(['git', '-C', str(self.repo), *args], capture_output=True)
        (self.repo / 'a.txt').write_text('hi', encoding='utf-8')
        subprocess.run(['git', '-C', str(self.repo), 'add', 'a.txt'], capture_output=True)
        subprocess.run(
            ['git', '-C', str(self.repo), 'commit', '-qm', 'init'], capture_output=True,
        )
        self.marker = Path(self._tmp.name) / 'EXECUTED'

    def _poison(self, key: str) -> None:
        subprocess.run(
            ['git', '-C', str(self.repo), 'config', key, f'touch {self.marker}'],
            capture_output=True,
        )

    def test_fsmonitor_does_not_run_on_status(self) -> None:
        self._poison('core.fsmonitor')
        subprocess.run(
            build_safe_git_command(str(self.repo), ['status', '--porcelain']),
            capture_output=True, timeout=60,
        )
        self.assertFalse(
            self.marker.exists(),
            'core.fsmonitor from repo-local config executed on the host',
        )

    def test_ssh_command_does_not_run_on_remote_access(self) -> None:
        self._poison('core.sshCommand')
        subprocess.run(
            build_safe_git_command(
                str(self.repo), ['ls-remote', 'ssh://example.invalid/x'],
            ),
            capture_output=True, timeout=60,
        )
        self.assertFalse(
            self.marker.exists(),
            'core.sshCommand from repo-local config executed on the host',
        )


if __name__ == '__main__':
    unittest.main()
