"""Git access levels: strict (the default) and local.

One list decides both enforcement layers — the CLI deny flag and the
content-aware guard — so these tests pin the list and the guard against the
same level. Local releases the history of the checked-out branch and nothing
else: the remote, leaving the branch, config and the plumbing stay refused.
"""
from __future__ import annotations

import unittest

from agent_core_lib.agent_core_lib.helpers.command_floor import (
    GIT_ACCESS_LOCAL,
    GIT_ACCESS_STRICT,
    GIT_LOCAL_HISTORY_SUBCOMMANDS,
    GIT_MUTATING_SUBCOMMANDS,
    effective_git_access,
    git_access_prompt_text,
    git_denied_subcommands,
    normalized_git_access,
)
from agent_core_lib.agent_core_lib.helpers.command_policy import (
    CommandPolicy,
    Decision,
    classify_action,
    is_git_hard_reset,
)

_POLICY = CommandPolicy.secure_default()
_STAYS_THE_ORCHESTRATORS = (
    'push', 'pull', 'fetch', 'clone', 'remote', 'send-pack', 'receive-pack',
    'checkout', 'switch', 'branch', 'bisect', 'symbolic-ref',
    'config', 'worktree', 'submodule', 'update-ref', 'commit-tree',
    'filter-branch', 'gc', 'prune',
)


def _verdict(command: str, git_access: str = ''):
    return classify_action('Bash', {'command': command}, policy=_POLICY, git_access=git_access)


class LevelNamesTests(unittest.TestCase):
    def test_only_an_exact_local_is_local(self) -> None:
        self.assertEqual(normalized_git_access('local'), GIT_ACCESS_LOCAL)
        self.assertEqual(normalized_git_access('  LOCAL '), GIT_ACCESS_LOCAL)
        for value in ('strict', '', None, 'full', 'loose', 'true', 0):
            with self.subTest(value=value):
                self.assertEqual(normalized_git_access(value), GIT_ACCESS_STRICT)

    def test_nobody_watching_means_strict(self) -> None:
        self.assertEqual(effective_git_access('local', unsupervised=True), GIT_ACCESS_STRICT)
        self.assertEqual(effective_git_access('local', unsupervised=False), GIT_ACCESS_LOCAL)
        self.assertEqual(effective_git_access('', unsupervised=False), GIT_ACCESS_STRICT)


class DeniedListTests(unittest.TestCase):
    def test_local_only_ever_subtracts_from_the_strict_list(self) -> None:
        for sub in GIT_LOCAL_HISTORY_SUBCOMMANDS:
            self.assertIn(sub, GIT_MUTATING_SUBCOMMANDS, sub)

    def test_strict_is_the_whole_list(self) -> None:
        self.assertEqual(git_denied_subcommands(), GIT_MUTATING_SUBCOMMANDS)
        self.assertEqual(git_denied_subcommands('strict'), GIT_MUTATING_SUBCOMMANDS)
        self.assertEqual(git_denied_subcommands('nonsense'), GIT_MUTATING_SUBCOMMANDS)

    def test_local_releases_the_history_commands_and_nothing_else(self) -> None:
        denied = git_denied_subcommands('local')
        for sub in GIT_LOCAL_HISTORY_SUBCOMMANDS:
            self.assertNotIn(sub, denied, sub)
        for sub in _STAYS_THE_ORCHESTRATORS:
            self.assertIn(sub, denied, sub)
        self.assertEqual(
            set(GIT_MUTATING_SUBCOMMANDS) - set(denied), set(GIT_LOCAL_HISTORY_SUBCOMMANDS),
        )


class PromptTextTests(unittest.TestCase):
    def test_strict_adds_nothing(self) -> None:
        self.assertEqual(git_access_prompt_text('strict'), '')
        self.assertEqual(git_access_prompt_text(''), '')

    def test_local_names_every_released_command_and_the_limits(self) -> None:
        text = git_access_prompt_text('local')
        for sub in GIT_LOCAL_HISTORY_SUBCOMMANDS:
            self.assertIn(f'`git {sub}`', text, sub)
        for limit in ('`git push`', '`git checkout`', '`git config`', '`git reset --hard`'):
            self.assertIn(limit, text, limit)
        self.assertIn('overrides', text)


class GuardAtLocalTests(unittest.TestCase):
    def test_the_history_commands_pass(self) -> None:
        for command in (
            'git commit -m "fix the parser"',
            'git commit --amend --no-edit',
            'git rebase main',
            'git rebase -i HEAD~3',
            'git merge origin/main',
            'git cherry-pick abc1234',
            'git revert --no-edit HEAD',
            'git am change.patch',
            'git tag v1.2',
            'git reset --soft HEAD~1',
            'git reset HEAD -- src/a.py',
        ):
            with self.subTest(command=command):
                verdict = _verdict(command, 'local')
                self.assertEqual(verdict.decision, Decision.ALLOW, verdict.reason)

    def test_a_hard_reset_asks(self) -> None:
        for command in ('git reset --hard', 'git reset --hard origin/main',
                        'git -C repo reset --hard HEAD~2'):
            with self.subTest(command=command):
                verdict = _verdict(command, 'local')
                self.assertEqual(verdict.decision, Decision.ASK)
                self.assertEqual(verdict.rule_id, 'fs.git_reset_hard')

    def test_the_orchestrators_commands_still_block(self) -> None:
        for sub in ('push', 'pull', 'fetch', 'checkout main', 'switch main',
                    'branch other', 'config core.hooksPath x', 'update-ref HEAD x',
                    'remote add evil https://x', 'gc'):
            with self.subTest(sub=sub):
                verdict = _verdict(f'git {sub}', 'local')
                self.assertEqual(verdict.decision, Decision.BLOCK)

    def test_a_wrapper_does_not_get_a_push_past_it(self) -> None:
        for command in ("sh -c 'git push origin HEAD'", 'env git push',
                        '/usr/bin/git push', 'eval "git push"'):
            with self.subTest(command=command):
                self.assertEqual(_verdict(command, 'local').decision, Decision.BLOCK)

    def test_a_wrapped_commit_passes_like_a_plain_one(self) -> None:
        self.assertEqual(_verdict("sh -c 'git commit -m x'", 'local').decision, Decision.ALLOW)


class GuardAtStrictTests(unittest.TestCase):
    def test_strict_is_the_default_and_blocks_history(self) -> None:
        for command in ('git commit -m x', 'git rebase main', 'git reset --soft HEAD~1'):
            with self.subTest(command=command):
                verdict = _verdict(command)
                self.assertEqual(verdict.decision, Decision.BLOCK)
                self.assertEqual(verdict.rule_id, 'git.orchestrator_owned')

    def test_an_unknown_level_blocks_like_strict(self) -> None:
        self.assertEqual(_verdict('git commit -m x', 'full').decision, Decision.BLOCK)

    def test_the_guard_reads_the_same_list_as_the_floor(self) -> None:
        # It used to keep its own set and lacked these; a transport with no
        # deny flag (Codex) then let through what Claude's floor refused.
        for sub in ('gc', 'prune', 'init', 'bundle', 'notes', 'pack-refs'):
            with self.subTest(sub=sub):
                self.assertEqual(_verdict(f'git {sub}').decision, Decision.BLOCK)


class HardResetShapeTests(unittest.TestCase):
    def test_what_counts(self) -> None:
        self.assertTrue(is_git_hard_reset('git reset --hard'))
        self.assertTrue(is_git_hard_reset('git -c core.x=1 reset --hard HEAD~1'))
        for segment in ('git reset --soft HEAD~1', 'git reset', 'git restore --hard',
                        'echo git reset --hard', 'grep -rn "git reset --hard" docs'):
            with self.subTest(segment=segment):
                self.assertFalse(is_git_hard_reset(segment))


if __name__ == '__main__':
    unittest.main()
