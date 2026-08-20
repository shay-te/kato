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
    _git_revert_pathspecs,
    classify_action,
    git_revert_breadth,
    git_subcommand_of,
)
from agent_core_lib.agent_core_lib.helpers.command_introspection import (
    split_command_segments,
)

_DEFAULT = CommandPolicy.secure_default()


def _flags(command: str) -> bool:
    # Split the way the real pipeline does. The rule is anchored on the
    # segment's PROGRAM, so handing it an unsplit ``a && b`` would test a
    # shape production never produces.
    return bool(_detect_git_whole_tree_revert(split_command_segments(command)))


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


class ReproducedBypassRegressionTests(unittest.TestCase):
    """Every command below reverted a real scratch repo while classified ALLOW.

    The first version of this rule enumerated the pathspecs that mean
    "everything" and treated everything else as scoped — a denylist against
    git's open-ended magic-pathspec grammar. An adversarial pass broke it
    fifteen different ways, each reproduced against real git.

    The fix inverts the test: a pathspec counts as scoped only if it is
    demonstrably a plain narrow path. These cases are kept as the record of
    why, because the enumerating version looked entirely reasonable.
    """

    # An exclude-only pathspec matches the WHOLE tree.
    MAGIC_PATHSPECS = (
        "git restore ':!nonexistent'",
        "git restore ':(exclude)zzz'",
        "git restore ':'",
        "git restore ':(attr:!binary)'",
        "git restore ':(glob)**'",
        "git restore ':(glob)**/*'",
        "git restore ':(glob)*'",
        "git restore ':(top)'",
    )
    # A pre-command option the old verb regex did not model de-anchored the
    # whole match, so the command was never even examined.
    PRE_COMMAND_OPTIONS = (
        'git -P restore .',
        'git -p restore .',
        'git --work-tree . restore .',
        'git --namespace foo restore .',
        'git --git-dir .git restore .',
    )
    # Paths that normalize to the tree root, and globs.
    PATH_NORMALIZATION = (
        'git restore ./.',
        'git restore ../',
        'git restore ./*',
        'git restore "src/.."',
        'git restore $(pwd)',
    )
    # Parser tricks: a trailing comment supplied fake pathspecs, subshell
    # parens rode along in the token, and ``-s`` swallowed the pathspec.
    PARSER_TRICKS = (
        'git restore --pathspec-from-file=/tmp/all.txt # revert everything',
        'git restore --pathspec-from-file /tmp/all.txt',
        '(git restore .)',
        "sh -c 'git restore .'",
    )
    # The two ``git checkout`` forms that used to live in PARSER_TRICKS are
    # now BLOCKED outright rather than asked: checkout is orchestrator-owned
    # (it moves HEAD), so it never reaches the pathspec-breadth rule. That
    # is strictly stronger — see
    # ``test_checkout_forms_are_blocked_before_breadth_is_considered``.
    BLOCKED_CHECKOUT_TRICKS = (
        'git checkout -- -s .',
        'echo . | git checkout --pathspec-from-file=-',
    )

    def test_checkout_forms_are_blocked_before_breadth_is_considered(self) -> None:
        for command in self.BLOCKED_CHECKOUT_TRICKS:
            with self.subTest(command=command):
                verdict = _verdict(command)
                self.assertEqual(verdict.decision, Decision.BLOCK, command)
                self.assertEqual(verdict.rule_id, 'git.orchestrator_owned')

    def _assert_all_ask(self, commands, why: str) -> None:
        for command in commands:
            with self.subTest(command=command):
                verdict = _verdict(command)
                self.assertEqual(
                    verdict.decision, Decision.ASK,
                    f'{command!r} {why} — it must reach the operator',
                )
                self.assertEqual(verdict.rule_id, 'fs.git_revert_all')

    def test_magic_pathspecs(self) -> None:
        self._assert_all_ask(
            self.MAGIC_PATHSPECS, 'reverts the whole tree via pathspec magic',
        )

    def test_pre_command_options(self) -> None:
        self._assert_all_ask(
            self.PRE_COMMAND_OPTIONS, 'reverts the whole tree',
        )

    def test_path_normalization(self) -> None:
        self._assert_all_ask(
            self.PATH_NORMALIZATION, 'resolves to the whole tree',
        )

    def test_parser_tricks(self) -> None:
        self._assert_all_ask(
            self.PARSER_TRICKS, 'defeats naive argv tokenization',
        )

    def test_an_unreadable_pathspec_set_fails_closed(self) -> None:
        # --pathspec-from-file names a file we cannot read, so breadth is
        # unknowable. Unknown must mean ASK, never ALLOW.
        self.assertTrue(_flags('git restore --pathspec-from-file=/tmp/x.txt'))


class MentionsAreNotInvocationsTests(unittest.TestCase):
    """False positives are a security bug too.

    The substring-matching version flagged any command whose TEXT contained
    "git restore ." — writing docs about it, grepping for it. Popups for
    things that are not happening are how an operator learns to click through
    the popup that matters.
    """

    MENTIONS = (
        'grep -rn "git restore ." docs/',
        'echo "run git restore . to undo"',
        "echo 'to undo, run git restore .' >> docs/undo.md",
        'rg --files-with-matches "git restore ."',
    )

    def test_merely_naming_the_command_is_not_running_it(self) -> None:
        for command in self.MENTIONS:
            with self.subTest(command=command):
                self.assertFalse(
                    _flags(command),
                    f'{command!r} only MENTIONS the command; flagging it is '
                    'noise that erodes the value of the approval prompt',
                )


class BreadthApiTests(unittest.TestCase):
    """The public helpers the remembered-permission key depends on."""

    def test_git_subcommand_is_extracted_past_pre_command_options(self) -> None:
        self.assertEqual(git_subcommand_of('git -C /r -c a.b=c restore x'), 'restore')
        self.assertEqual(git_subcommand_of('git -P status'), 'status')
        self.assertEqual(git_subcommand_of('echo git restore .'), '')
        self.assertEqual(git_subcommand_of('npm test'), '')

    def test_breadth_distinguishes_scoped_from_whole_tree(self) -> None:
        self.assertEqual(git_revert_breadth('git restore src/a.js'), 'scoped')
        self.assertEqual(git_revert_breadth('git restore .'), 'whole-tree')
        self.assertEqual(git_revert_breadth('git status'), '')
        # ``git checkout main`` is branch movement, but git's own grammar
        # cannot tell a branch name from a path here either. It reads as
        # scoped, which is harmless: the whole-tree answer is the only one
        # that changes a decision, and branch movement is denied at Layer A.
        self.assertNotEqual(git_revert_breadth('git checkout main'), 'whole-tree')


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
    """``_git_revert_pathspecs`` must return pathspecs and nothing else."""

    @staticmethod
    def _paths(command: str) -> list:
        return _git_revert_pathspecs(command)[1]

    def test_flags_and_separators_are_dropped(self) -> None:
        self.assertEqual(
            self._paths('git restore --staged --worktree -- src/a.js'),
            ['src/a.js'],
        )

    def test_source_consumes_its_tree_ish_in_both_forms(self) -> None:
        # A commit name must never be mistaken for a pathspec — in the
        # separated form it would otherwise land in the pathspec list and
        # make a whole-tree revert look scoped.
        self.assertEqual(
            self._paths('git restore --source HEAD~2 src/a.js'), ['src/a.js'],
        )
        self.assertEqual(
            self._paths('git restore --source=HEAD~2 src/a.js'), ['src/a.js'],
        )
        self.assertEqual(self._paths('git restore --source HEAD~2 .'), ['.'])

    def test_pre_command_options_are_dropped(self) -> None:
        self.assertEqual(
            self._paths('git -C /repo -c core.pager=cat restore src/a.js'),
            ['src/a.js'],
        )

    def test_everything_after_the_separator_is_a_pathspec(self) -> None:
        # Past ``--`` a flag-looking token IS a path. Continuing to parse
        # options there let ``-s`` consume the real pathspec.
        self.assertEqual(self._paths('git checkout -- -s .'), ['-s', '.'])

    def test_quotes_are_stripped_so_a_quoted_dot_is_still_a_dot(self) -> None:
        self.assertEqual(self._paths("git restore '.'"), ['.'])


if __name__ == '__main__':
    unittest.main()
