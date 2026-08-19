"""What the agent may actually do with git.

Reported from the field: "blocking entire git is not correct — to revert a
file he needs git diff and revert, which he can't", and "I want to tell him
to put a file back to the previous 2 commits". Both were TRUE as symptoms
and WRONG as diagnoses: read-only git and ``git restore`` were never denied
by the floor. What the agent was reading was the PROMPT, which listed only
prohibitions, so it generalised to "the orchestration layer forbids me from
running any git command" and refused.

So this pins both halves: the floor lets the commands through, and the
prompt tells the agent they exist. A prompt that only forbids is why an
operator's "just revert that file" got refused.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    repository_scope_text,
)
from agent_core_lib.agent_core_lib.helpers.command_policy import (
    CommandPolicy,
    Decision,
    classify_action,
)
from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient

_POLICY = CommandPolicy.secure_default()


def _floor_items(bypass: bool = False) -> list[str]:
    return ClaudeCliClient._merge_disallowed_with_floor(
        '', bypass_permissions=bypass,
    ).split(',')


class HistoryInspectionIsAvailableTests(unittest.TestCase):
    """"Check previous git commits for file history" must work."""

    HISTORY_COMMANDS = (
        'git log --oneline -20',
        'git log --follow -- src/messaging_client.py',
        'git show HEAD~2:src/messaging_client.py',
        'git diff HEAD~2 -- src/messaging_client.py',
        'git blame src/messaging_client.py',
        'git status --porcelain',
        'git rev-list --max-count=5 HEAD',
    )

    def test_the_floor_does_not_deny_them(self) -> None:
        items = _floor_items()
        for command in self.HISTORY_COMMANDS:
            subcommand = command.split()[1]
            with self.subTest(subcommand=subcommand):
                self.assertNotIn(f'Bash(git {subcommand}:*)', items)

    def test_the_guard_does_not_flag_them(self) -> None:
        for command in self.HISTORY_COMMANDS:
            with self.subTest(command=command):
                verdict = classify_action('Bash', {'command': command}, policy=_POLICY)
                self.assertEqual(verdict.rule_id, '', command)


class RestoreFromHistoryIsAvailableTests(unittest.TestCase):
    """"Put a file back to the previous 2 commits" must work.

    Also the reported case: bringing a deleted messaging client and rabbit
    listener back into the admin repo, which is a restore from history.
    """

    RESTORES = (
        'git restore --source=HEAD~2 -- src/app.js',
        'git restore --source=HEAD~5 -- src/messaging_client.py',
        'git restore --source=abc1234 -- src/rabbit_listener.py',
        'git restore --source HEAD~2 -- src/app.js',
    )

    def test_the_floor_permits_restore(self) -> None:
        items = _floor_items()
        self.assertNotIn('Bash(git restore:*)', items)

    def test_a_scoped_restore_from_history_is_not_flagged(self) -> None:
        for command in self.RESTORES:
            with self.subTest(command=command):
                verdict = classify_action('Bash', {'command': command}, policy=_POLICY)
                self.assertEqual(verdict.rule_id, '', command)

    def test_a_whole_tree_restore_from_history_still_asks(self) -> None:
        # The one limit: naming a commit does not license discarding the
        # entire uncommitted task output.
        verdict = classify_action(
            'Bash', {'command': 'git restore --source=HEAD~2 .'}, policy=_POLICY,
        )
        self.assertEqual(verdict.decision, Decision.ASK)
        self.assertEqual(verdict.rule_id, 'fs.git_revert_all')


class OnlyBranchAndPublishAreBlockedTests(unittest.TestCase):
    """"Only block those" — the orchestrator owns branch state and publishing."""

    def test_the_orchestrator_owned_commands_stay_denied(self) -> None:
        items = _floor_items()
        for subcommand in ('commit', 'push', 'pull', 'fetch', 'merge',
                           'rebase', 'reset', 'checkout', 'switch', 'branch'):
            with self.subTest(subcommand=subcommand):
                self.assertIn(f'Bash(git {subcommand}:*)', items)


class ThePromptSaysWhatIsAvailableTests(unittest.TestCase):
    """A prompt that only forbids gets read as "no git at all"."""

    def _text(self) -> str:
        prepared = SimpleNamespace(
            repositories=[SimpleNamespace(
                id='admin', local_path='/w/admin', destination_branch='main',
            )],
            repository_branches={},
            branch_name='feature/x',
        )
        task = SimpleNamespace(branch_name='feature/x', repositories=[])
        return repository_scope_text(task, prepared)

    def test_it_names_the_history_commands_the_agent_may_run(self) -> None:
        text = self._text()
        for phrase in ('git log', 'git show', 'git diff', 'git blame'):
            self.assertIn(phrase, text)

    def test_it_shows_how_to_restore_from_an_earlier_commit(self) -> None:
        self.assertIn('git restore --source=<commit>', self._text())

    def test_it_still_forbids_branch_movement_and_publishing(self) -> None:
        text = self._text()
        for phrase in ('git commit', 'git push', 'git checkout', 'git branch'):
            self.assertIn(phrase, text)

    def test_it_does_not_read_as_a_blanket_git_ban(self) -> None:
        # The exact failure: the agent told an operator "the orchestration
        # layer forbids me from running any git command".
        self.assertIn('EVERYTHING ELSE IN GIT IS AVAILABLE', self._text())


if __name__ == '__main__':
    unittest.main()


class WorktreeToolsAreAvailableTests(unittest.TestCase):
    """"There are more actions that we miss with this blocking of git."

    The floor's rule is "the orchestrator owns BRANCH STATE and PUBLISHING".
    Denying every verb that can write anything is a broader rule than that,
    and it cost real work — an operator could not ask the agent to set
    changes aside, apply a patch, or find a commit it had lost.
    """

    def test_stash_apply_and_reflog_are_permitted(self) -> None:
        items = _floor_items()
        for subcommand in ('stash', 'apply', 'reflog'):
            with self.subTest(subcommand=subcommand):
                self.assertNotIn(f'Bash(git {subcommand}:*)', items)

    def test_the_ordinary_forms_are_not_flagged(self) -> None:
        for command in (
            'git stash', 'git stash list', 'git stash pop', 'git stash show -p',
            'git reflog', 'git reflog show',
            'git apply fix.diff', 'git apply -R fix.diff',
        ):
            with self.subTest(command=command):
                verdict = classify_action('Bash', {'command': command}, policy=_POLICY)
                self.assertEqual(verdict.rule_id, '', command)

    def test_the_destructive_forms_reach_the_operator(self) -> None:
        # Denying the whole verb to stop these cost more than it bought;
        # catching the FORM by argv keeps both halves.
        for command, rule in (
            ('git stash drop', 'fs.git_stash_destructive'),
            ('git stash clear', 'fs.git_stash_destructive'),
            ('git reflog expire --all', 'fs.git_reflog_destructive'),
            ('git reflog delete HEAD@{2}', 'fs.git_reflog_destructive'),
            ('git apply --unsafe-paths ../outside.diff', 'fs.git_apply_unsafe'),
        ):
            with self.subTest(command=command):
                verdict = classify_action('Bash', {'command': command}, policy=_POLICY)
                self.assertEqual(verdict.decision, Decision.ASK, command)
                self.assertEqual(verdict.rule_id, rule)

    def test_the_index_and_config_surfaces_stay_denied(self) -> None:
        # config is the hook/RCE surface, bisect moves HEAD, clean deletes
        # untracked files with no recovery, and add/rm/mv have plain-shell
        # equivalents while the orchestrator stages everything itself.
        items = _floor_items()
        for subcommand in ('config', 'bisect', 'clean', 'add', 'rm', 'mv'):
            with self.subTest(subcommand=subcommand):
                self.assertIn(f'Bash(git {subcommand}:*)', items)

    def test_the_prompt_mentions_them(self) -> None:
        prepared = SimpleNamespace(
            repositories=[SimpleNamespace(
                id='admin', local_path='/w/admin', destination_branch='main',
            )],
            repository_branches={},
            branch_name='feature/x',
        )
        text = repository_scope_text(
            SimpleNamespace(branch_name='feature/x', repositories=[]), prepared,
        )
        for phrase in ('git stash', 'git apply', 'git reflog'):
            self.assertIn(phrase, text)
