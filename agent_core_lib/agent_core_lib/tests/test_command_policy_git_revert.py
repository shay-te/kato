"""Layer B: reverting a FILE is routine; reverting the WHOLE TREE is not.

An operator asked the agent to revert one file and got "I can't, the
orchestration layer forbids any git command" — a refusal for an operation
the orchestrator does not own. ``git restore <path>`` is file-level
editing, not branch movement, so it was opened up.

What must not come with it: a silent whole-tree revert. The agent's work
is uncommitted until the publish step, so ``git restore .`` destroys the
entire task with no commit and no reflog entry to recover from. That case
routes to the operator instead.

The split is on PATHSPEC BREADTH, not on the subcommand name.
"""

from __future__ import annotations

import unittest

from agent_core_lib.agent_core_lib.helpers.command_policy import (
    CommandPolicy,
    Decision,
    RiskCategory,
    _detect_git_whole_tree_revert,
    _git_revert_targets,
    classify_action,
)

_DEFAULT = CommandPolicy.secure_default()


def _flags(command: str) -> bool:
    return bool(_detect_git_whole_tree_revert([command]))


def _verdict(command: str):
    return classify_action('Bash', {'command': command}, policy=_DEFAULT)


class ScopedRevertIsAllowedTests(unittest.TestCase):
    """The operator's actual request must go through."""

    SCOPED = (
        'git restore src/app.js',
        'git restore package-lock.json',
        'git restore src/a.js src/b.js',
        'git restore src/',
        'git restore --staged src/a.js',
        'git restore --worktree --staged src/a.js',
        'git restore --source=HEAD src/a.js',
        'git restore --source HEAD~2 src/a.js',
        'git checkout -- src/a.js',
        'git checkout -- ./src/a.js',
        'git restore ./src/a.js',
        'git restore src/./a.js',
        "git restore 'my folder/a.js'",
        'git -C /repo restore src/a.js',
    )

    def test_scoped_reverts_are_not_flagged(self) -> None:
        for command in self.SCOPED:
            with self.subTest(command=command):
                self.assertFalse(
                    _flags(command),
                    f'{command!r} is a path-scoped revert and must not be '
                    'treated as a whole-tree wipe — over-blocking this is '
                    'the exact bug the carve-out exists to fix',
                )

    def test_the_reported_case_from_the_field(self) -> None:
        # The operator's message was literally "just revert the changes
        # here" on one lockfile, in a Windows workspace clone.
        command = (
            'git -C C:/Codes/workspaces/PROJ-2818/admin-client '
            'restore package-lock.json'
        )
        self.assertFalse(_flags(command))


class WholeTreeRevertMustReachTheOperatorTests(unittest.TestCase):
    """Unrecoverable, so it never happens unseen."""

    WHOLE_TREE = (
        'git restore .',
        'git restore ./',
        'git restore',                      # pathspec-less restore == whole tree
        'git restore -- .',
        'git restore *',
        'git restore :/',
        'git restore --source=HEAD~2 .',
        'git restore --staged .',
        'git checkout -- .',
        "git checkout -- '.'",
        'git checkout -- ":/"',
        'git checkout -- :(top)',
        'git -C /repo restore .',
        'npm test && git restore .',        # per-segment, not just leading token
        'git restore . ; echo done',
    )

    def test_whole_tree_reverts_are_flagged(self) -> None:
        for command in self.WHOLE_TREE:
            with self.subTest(command=command):
                self.assertTrue(
                    _flags(command),
                    f'{command!r} discards every uncommitted change — the '
                    'whole task output — and must reach the operator',
                )

    def test_the_candidate_is_ask_not_block(self) -> None:
        # "Undo everything you did" is a legitimate thing for an operator to
        # approve. It must be seen, not forbidden — a floor BLOCK here would
        # just recreate the refusal this change removed, one level up.
        candidates = _detect_git_whole_tree_revert(['git restore .'])
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.category, RiskCategory.DESTRUCTIVE_FS)
        self.assertFalse(
            candidate.is_floor,
            'a whole-tree revert is dual-use, not a no-legitimate-use floor',
        )
        self.assertEqual(candidate.rule_id, 'fs.git_revert_all')

    def test_end_to_end_through_the_public_classifier(self) -> None:
        verdict = _verdict('git restore .')
        self.assertEqual(verdict.decision, Decision.ASK)
        self.assertEqual(verdict.category, RiskCategory.DESTRUCTIVE_FS)

    def test_end_to_end_a_scoped_revert_is_not_pushed_to_ask_by_this_rule(self) -> None:
        verdict = _verdict('git restore src/a.js')
        self.assertNotEqual(verdict.category, RiskCategory.DESTRUCTIVE_FS)


class BranchMovementIsNotThisRulesJobTests(unittest.TestCase):
    """Invariant A stays where it belongs.

    ``git checkout <branch>`` is branch movement — owned by the transport's
    ``--disallowedTools`` floor and the prompt, which still deny it in every
    mode including bypassPermissions. This classifier deliberately stays
    silent on it rather than half-duplicating that guarantee.
    """

    def test_branch_checkout_is_not_claimed_by_the_revert_rule(self) -> None:
        for command in ('git checkout main', 'git checkout -b feature', 'git switch main'):
            with self.subTest(command=command):
                self.assertFalse(_flags(command))


class TargetParsingTests(unittest.TestCase):
    """``_git_revert_targets`` must return pathspecs and nothing else."""

    def test_flags_and_separators_are_dropped(self) -> None:
        self.assertEqual(
            _git_revert_targets('git restore --staged --worktree -- src/a.js'),
            ['src/a.js'],
        )

    def test_source_consumes_its_tree_ish_in_both_forms(self) -> None:
        # A commit name must never be mistaken for a pathspec — in the
        # separated form it would otherwise land in targets and make a
        # whole-tree revert look scoped.
        self.assertEqual(
            _git_revert_targets('git restore --source HEAD~2 src/a.js'), ['src/a.js'],
        )
        self.assertEqual(
            _git_revert_targets('git restore --source=HEAD~2 src/a.js'), ['src/a.js'],
        )
        self.assertEqual(_git_revert_targets('git restore --source HEAD~2 .'), ['.'])

    def test_pre_command_options_are_dropped(self) -> None:
        self.assertEqual(
            _git_revert_targets('git -C /repo -c core.pager=cat restore src/a.js'),
            ['src/a.js'],
        )

    def test_quotes_are_stripped_so_a_quoted_dot_is_still_a_dot(self) -> None:
        self.assertEqual(_git_revert_targets("git restore '.'"), ['.'])
        self.assertEqual(_git_revert_targets('git restore ".""'.rstrip('"')), ['.'])


if __name__ == '__main__':
    unittest.main()
