"""The floor every transport shares, and the two ways it gets enforced.

The point of these tests is not the contents of the lists — it is that the
lists have exactly one home. A transport with a tool-deny flag renders them
into patterns; a transport without one renders them into prompt text. When
those were maintained separately, one said "git … anything" in prose while the
other enforced forty named subcommands, and nothing could tell you whether
they agreed.
"""

from __future__ import annotations

import unittest

from agent_core_lib.agent_core_lib.helpers.command_floor import (
    FLOOR_DENY_PROGRAMS,
    GIT_MUTATING_SUBCOMMANDS,
    UNSUPERVISED_DENY_SUBCOMMANDS,
    cli_deny_patterns,
    prompt_floor_rules,
)


class FloorContentTests(unittest.TestCase):
    def test_publishing_and_ref_moving_git_is_denied(self) -> None:
        for sub in ('push', 'commit', 'reset', 'checkout', 'switch', 'branch',
                    'remote', 'fetch', 'rebase', 'config'):
            self.assertIn(sub, GIT_MUTATING_SUBCOMMANDS, sub)

    def test_the_plumbing_that_reaches_the_same_capability_is_denied(self) -> None:
        # Listing only porcelain left commit-building and pushing reachable one
        # layer down: hash-object -w + mktree + commit-tree, then send-pack.
        for sub in ('hash-object', 'mktree', 'commit-tree', 'send-pack',
                    'checkout-index'):
            self.assertIn(sub, GIT_MUTATING_SUBCOMMANDS, sub)

    def test_read_only_git_is_never_denied(self) -> None:
        # The self-review workflow needs ``git diff master...branch``; denying
        # it had the agent reporting "git is forbidden" for asked-for work.
        for sub in ('status', 'log', 'diff', 'show', 'blame'):
            self.assertNotIn(sub, GIT_MUTATING_SUBCOMMANDS, sub)

    def test_worktree_operations_stay_with_the_agent(self) -> None:
        # File/worktree verbs, not branch-state ones: their destructive FORMS
        # are caught by argv in the content-aware guard instead.
        for sub in ('restore', 'stash', 'apply', 'reflog', 'add', 'rm', 'mv',
                    'clean'):
            self.assertNotIn(sub, GIT_MUTATING_SUBCOMMANDS, sub)

    def test_the_program_floor_stays_tiny_and_unambiguous(self) -> None:
        # A deny flag matches by program prefix, so a dual-use program here
        # would over-block real work. Those belong in the content-aware guard.
        for program in ('rm', 'chmod', 'dd', 'curl', 'mv', 'cp'):
            self.assertNotIn(program, FLOOR_DENY_PROGRAMS, program)
        for program in ('mkfs', 'mkswap', 'nsenter', 'unshare', 'chroot',
                        'shutdown', 'reboot', 'halt', 'poweroff'):
            self.assertIn(program, FLOOR_DENY_PROGRAMS, program)

    def test_restore_is_withdrawn_only_when_unattended(self) -> None:
        self.assertEqual(UNSUPERVISED_DENY_SUBCOMMANDS, ('restore',))
        self.assertNotIn('restore', GIT_MUTATING_SUBCOMMANDS)


class CliDenyPatternTests(unittest.TestCase):
    def test_both_accepted_forms_are_emitted(self) -> None:
        # CLI versions differ on which form they parse; a floor that silently
        # matches nothing is worse than no floor.
        patterns = cli_deny_patterns(('push',), program='git')

        self.assertEqual(patterns, ('Bash(git push:*)', 'Bash(git push *)'))

    def test_a_bare_program_needs_no_prefix(self) -> None:
        self.assertEqual(
            cli_deny_patterns(('mkfs',)), ('Bash(mkfs:*)', 'Bash(mkfs *)'),
        )

    def test_every_floor_token_is_rendered(self) -> None:
        patterns = cli_deny_patterns(GIT_MUTATING_SUBCOMMANDS, program='git')

        self.assertEqual(len(patterns), 2 * len(GIT_MUTATING_SUBCOMMANDS))
        for sub in GIT_MUTATING_SUBCOMMANDS:
            self.assertIn(f'Bash(git {sub}:*)', patterns, sub)

    def test_an_empty_floor_renders_nothing(self) -> None:
        self.assertEqual(cli_deny_patterns(()), ())


class PromptFloorRulesTests(unittest.TestCase):
    def test_it_names_each_category_the_deny_flag_enforces(self) -> None:
        text = prompt_floor_rules()

        for expected in ('push', 'commit', 'mkfs', 'nsenter', 'shutdown'):
            self.assertIn(expected, text, expected)

    def test_it_tells_the_agent_read_only_git_is_allowed(self) -> None:
        # Without this the prompt-only transport reads "no git at all" and
        # refuses the diff reading its own review workflow depends on.
        self.assertIn('Read-only git', prompt_floor_rules())

    def test_it_says_the_limits_are_enforced_elsewhere(self) -> None:
        # The model should understand these are not this transport's opinion.
        self.assertIn('refused at the tool level', prompt_floor_rules())


class OneSourceOfTruthTests(unittest.TestCase):
    """The regression this module exists to prevent."""

    def test_the_deny_flag_transport_derives_its_patterns_from_here(self) -> None:
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient

        self.assertEqual(
            ClaudeCliClient.GIT_DENY_PATTERNS,
            cli_deny_patterns(GIT_MUTATING_SUBCOMMANDS, program='git'),
        )
        self.assertEqual(
            ClaudeCliClient.ACTION_GUARD_DENY_PATTERNS,
            cli_deny_patterns(FLOOR_DENY_PROGRAMS),
        )
        self.assertEqual(
            ClaudeCliClient.UNSUPERVISED_DENY_PATTERNS,
            cli_deny_patterns(UNSUPERVISED_DENY_SUBCOMMANDS, program='git'),
        )

    def test_the_prompt_only_transport_states_the_same_floor(self) -> None:
        from codex_core_lib.codex_core_lib.cli_client import CodexCliClient

        self.assertIn(prompt_floor_rules(), CodexCliClient._tool_guardrails_text())


if __name__ == '__main__':
    unittest.main()
