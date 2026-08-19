"""Tests for the non-overridable git denylist enforced on every Claude spawn.

Kato is the only component that runs git operations. Claude must never
invoke git directly, regardless of operator-supplied tool config or
permission mode.
"""

from __future__ import annotations

import unittest

from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient


class GitDenylistMergeTests(unittest.TestCase):
    def test_empty_operator_disallowed_still_denies_git(self) -> None:
        merged = ClaudeCliClient._merge_disallowed_with_git_deny('')
        for pattern in ClaudeCliClient.GIT_DENY_PATTERNS:
            self.assertIn(pattern, merged.split(','))

    def test_mutating_git_is_denied(self) -> None:
        # push/commit/reset/… stay hard-denied so Claude can't race kato.
        items = ClaudeCliClient._merge_disallowed_with_git_deny('').split(',')
        for sub in ('push', 'commit', 'reset', 'checkout', 'rebase',
                    'branch', 'merge', 'fetch', 'pull', 'clone'):
            self.assertIn(f'Bash(git {sub}:*)', items, sub)

    def test_git_restore_is_NOT_denied_so_a_file_can_be_reverted(self) -> None:
        # An operator asked the agent to revert one file and got back "I
        # can't, the orchestration layer forbids any git command" — a refusal
        # for something the orchestrator does not own.
        #
        # ``git restore`` is the one file-scoped member of this family: it
        # cannot move HEAD or switch branches, so permitting it cannot race
        # the branch state machine. The residual risk (``git restore .``
        # discarding the whole task) is caught by argv in Layer B
        # (agent_core_lib.command_policy, rule fs.git_revert_all), which can
        # tell a path from a whole-tree pathspec — something a prefix-matching
        # ``--disallowedTools`` entry fundamentally cannot do.
        items = ClaudeCliClient._merge_disallowed_with_git_deny('').split(',')
        self.assertNotIn('Bash(git restore:*)', items)
        self.assertNotIn('Bash(git restore *)', items)

    def test_branch_movement_is_still_denied_alongside_restore(self) -> None:
        # The reason ``restore`` could be opened up is precisely that these
        # stay shut. If a later change lets ``checkout``/``switch`` through,
        # the agent can move HEAD mid-task and race the orchestrator.
        items = ClaudeCliClient._merge_disallowed_with_git_deny('').split(',')
        for sub in ('checkout', 'switch', 'reset', 'branch', 'commit', 'push'):
            self.assertIn(f'Bash(git {sub}:*)', items, sub)

    def test_read_only_git_is_NOT_denied(self) -> None:
        # status/log/diff/show/blame must fall through to the approval prompt
        # so the self-review workflow can read the branch diff.
        merged = ClaudeCliClient._merge_disallowed_with_git_deny('')
        items = merged.split(',')
        for sub in ('status', 'log', 'diff', 'show', 'blame', 'rev-parse',
                    'ls-files', 'shortlog'):
            self.assertNotIn(f'Bash(git {sub}:*)', items, sub)
            self.assertNotIn(f'Bash(git {sub} *)', items, sub)
        # And the old catch-all that denied ALL git is gone.
        self.assertNotIn('Bash(git:*)', items)
        self.assertNotIn('Bash(git *)', items)

    def test_operator_extension_is_preserved(self) -> None:
        merged = ClaudeCliClient._merge_disallowed_with_git_deny('Bash(rm:*),WebFetch')
        items = merged.split(',')
        self.assertIn('Bash(rm:*)', items)
        self.assertIn('WebFetch', items)
        for pattern in ClaudeCliClient.GIT_DENY_PATTERNS:
            self.assertIn(pattern, items)

    def test_git_patterns_are_not_duplicated(self) -> None:
        already = ClaudeCliClient.GIT_DENY_PATTERNS[0]
        merged = ClaudeCliClient._merge_disallowed_with_git_deny(already)
        items = merged.split(',')
        self.assertEqual(items.count(already), 1)

    def test_operator_cannot_remove_git_patterns_via_omission(self) -> None:
        merged = ClaudeCliClient._merge_disallowed_with_git_deny('OnlyMyTool')
        # Even though the operator's value didn't include git, the merge
        # adds the git patterns. There is no operator input shape that
        # produces a merged string lacking the git patterns.
        for pattern in ClaudeCliClient.GIT_DENY_PATTERNS:
            self.assertIn(pattern, merged)


class CommandIncludesGitDenyTests(unittest.TestCase):
    def _build(self, **kwargs) -> list[str]:
        client = ClaudeCliClient(binary='claude', **kwargs)
        return client._build_command(additional_dirs=[], agent_session_id='')

    def test_safe_mode_command_includes_git_deny(self) -> None:
        command = self._build(bypass_permissions=False)
        # --disallowedTools is always present now (was conditional before).
        self.assertIn('--disallowedTools', command)
        idx = command.index('--disallowedTools')
        flag_value = command[idx + 1]
        for pattern in ClaudeCliClient.GIT_DENY_PATTERNS:
            self.assertIn(pattern, flag_value)

    def test_bypass_mode_command_still_includes_git_deny(self) -> None:
        command = self._build(bypass_permissions=True)
        self.assertIn('--disallowedTools', command)
        idx = command.index('--disallowedTools')
        flag_value = command[idx + 1]
        for pattern in ClaudeCliClient.GIT_DENY_PATTERNS:
            self.assertIn(
                pattern, flag_value,
                'git deny patterns must apply even when bypass_permissions=True',
            )

    def test_operator_disallowed_tools_combined_with_git_deny(self) -> None:
        command = self._build(disallowed_tools='WebFetch')
        idx = command.index('--disallowedTools')
        flag_value = command[idx + 1]
        self.assertIn('WebFetch', flag_value)
        for pattern in ClaudeCliClient.GIT_DENY_PATTERNS:
            self.assertIn(pattern, flag_value)



class UnsupervisedModeWithdrawsRestoreTests(unittest.TestCase):
    """``git restore`` is permitted because Layer B can route the dangerous
    form to the operator. In ``bypassPermissions`` no per-tool prompt fires,
    so Layer B never runs and there is nobody to route to — the capability
    is withdrawn in exactly the mode that cannot supervise it.
    """

    def _flag_value(self, **kwargs) -> str:
        client = ClaudeCliClient(binary='claude', **kwargs)
        command = client._build_command(additional_dirs=[], agent_session_id='')
        return command[command.index('--disallowedTools') + 1]

    def test_attended_mode_allows_restore(self) -> None:
        # The operator's actual request: revert a file while they are watching.
        items = self._flag_value(bypass_permissions=False).split(',')
        self.assertNotIn('Bash(git restore:*)', items)

    def test_bypass_mode_denies_restore(self) -> None:
        items = self._flag_value(bypass_permissions=True).split(',')
        self.assertIn(
            'Bash(git restore:*)', items,
            'in bypassPermissions the content-aware guard never runs, so an '
            'unrecoverable "git restore ." would execute with nobody watching',
        )
        self.assertIn('Bash(git restore *)', items)

    def test_bypass_mode_keeps_every_other_floor_entry(self) -> None:
        items = self._flag_value(bypass_permissions=True).split(',')
        for pattern in ClaudeCliClient.GIT_DENY_PATTERNS:
            self.assertIn(pattern, items)
        for pattern in ClaudeCliClient.ACTION_GUARD_DENY_PATTERNS:
            self.assertIn(pattern, items)

    def test_plumbing_subcommands_are_denied_in_both_modes(self) -> None:
        # hash-object + mktree + commit-tree builds a commit and send-pack
        # publishes it — the porcelain denials alone left that path open.
        for bypass in (False, True):
            items = self._flag_value(bypass_permissions=bypass).split(',')
            for sub in ('hash-object', 'commit-tree', 'send-pack',
                        'read-tree', 'checkout-index', 'mktree'):
                self.assertIn(f'Bash(git {sub}:*)', items, f'{sub} bypass={bypass}')


if __name__ == '__main__':
    unittest.main()
