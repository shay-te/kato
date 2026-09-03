"""A clone that was never branch-prepped must not be a dead end.

The classic mid-task repository: added after the task started, so nothing
ever moved its clone onto the task branch. The agent then works on
``master``, and every push reports

    push skipped <repo>: clone is on 'master', not the task branch
    'UNA-3040' — nothing was committed to the task branch, so there is
    nothing to push

which is accurate and useless. The operator's changes are sitting right
there in the working tree; the only thing between them and a pull request is
a checkout. Reported as "what is that, I need a pull request for this repo".

The recovery is deliberately narrow. A plain ``git checkout`` carries a dirty
tree across, so uncommitted work — the normal case — arrives intact. A clone
with its OWN COMMITS on the wrong branch is refused: those commits would stay
behind, and moving them is a rebase-or-cherry-pick with a real chance of
losing work, which is not something to do silently behind a button labelled
"push".

WHY THIS FILE STUBS AT THE GIT-COMMAND LEVEL
--------------------------------------------
The first version of these tests stubbed ``_prepare_task_branch`` and
asserted it was called. Every test passed. The shipped code then DESTROYED
41 files of an operator's uncommitted work, because ``_prepare_task_branch``
is the START-OF-TASK path: on a dirty tree it calls
``_make_git_ready_for_work``, which runs ``checkout -f`` →
``reset --hard origin/<destination>`` → ``clean -fd`` and keeps no stash.

The stub made a data-destroying call look like a checkout, so the suite was
blind to the only thing that mattered. These tests now record the actual
argv handed to git and assert on it, which is the layer where "moved the
branch" and "deleted the operator's work" finally look different.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from kato_core_lib.data_layers.service.repository_service import RepositoryService

# Every git subcommand/flag that can discard uncommitted work. If the
# recovery ever emits one of these, it is not a recovery.
DESTRUCTIVE = (
    ('reset', '--hard'),
    ('clean', '-fd'),
    ('clean', '-f'),
    ('checkout', '-f'),
    ('checkout', '--force'),
    ('stash',),
    ('restore',),
)


def _is_destructive(argv: list[str]) -> bool:
    return any(all(token in argv for token in pattern) for pattern in DESTRUCTIVE)


class _Service(RepositoryService):
    """Only the git seams the recovery touches — stubbed at argv level."""

    def __init__(self, *, current_branch, ahead, git_error=None, branch_exists=False):
        self._current_branch_value = current_branch
        self._ahead_value = ahead
        self._git_error = git_error
        self._branch_exists = branch_exists
        self.git_calls: list[list[str]] = []
        self.logger = SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None,
            exception=lambda *a, **k: None, error=lambda *a, **k: None,
        )

    def _resolve_branch_state(self, repository, branch_name):
        return ('/clone', self._current_branch_value)

    def destination_branch(self, repository):
        return 'master'

    def _comparison_reference(self, local_path, destination_branch):
        return 'origin/master'

    def _ahead_count(self, local_path, reference, branch):
        return self._ahead_value

    # The two real seams onto git. Nothing below them is stubbed, so the
    # argv these record is the argv the recovery would really run.
    def _run_git(self, local_path, args, failure_message, repository=None):
        self.git_calls.append(list(args))
        if self._git_error:
            raise RuntimeError(f'{failure_message}: {self._git_error}')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    def _git_stdout(self, local_path, args, failure_message, repository=None):
        self.git_calls.append(list(args))
        if args[:2] == ['branch', '--list']:
            return f'  {args[2]}' if self._branch_exists else ''
        return ''

    @property
    def checkouts(self) -> list[list[str]]:
        return [call for call in self.git_calls if call and call[0] == 'checkout']


def _repo():
    return SimpleNamespace(id='objective_love_core_lib', local_path='/clone')


class RecoverCloneOntoTaskBranchTests(unittest.TestCase):
    def test_a_never_prepped_clone_is_moved_onto_the_task_branch(self) -> None:
        # THE REPORTED CASE: on master, uncommitted work, no commits of its
        # own. A checkout carries the dirty tree across.
        service = _Service(current_branch='master', ahead=0)
        self.assertEqual(
            service.recover_clone_onto_task_branch(_repo(), 'UNA-3040'), '',
        )
        self.assertEqual(service.checkouts, [['checkout', '-b', 'UNA-3040']])

    def test_the_recovery_NEVER_issues_a_work_destroying_git_command(self) -> None:
        # The regression that cost an operator 41 files. `reset --hard` and
        # `clean -fd` reached git through _prepare_task_branch, whose NAME
        # reads like a checkout. Assert on argv, not on which helper ran.
        service = _Service(current_branch='master', ahead=0)
        service.recover_clone_onto_task_branch(_repo(), 'UNA-3040')
        self.assertTrue(service.git_calls, 'recovery issued no git command at all')
        for call in service.git_calls:
            self.assertFalse(
                _is_destructive(call),
                f'recovery would discard uncommitted work: git {" ".join(call)}',
            )

    def test_the_checkout_is_never_forced(self) -> None:
        # Without -f, git REFUSES a checkout that would clobber local
        # changes. That refusal is the last line of defence, and -f removes
        # it — so its absence is asserted on its own.
        service = _Service(current_branch='master', ahead=0)
        service.recover_clone_onto_task_branch(_repo(), 'UNA-3040')
        for call in service.checkouts:
            self.assertNotIn('-f', call)
            self.assertNotIn('--force', call)

    def test_an_existing_local_branch_is_switched_to_not_recreated(self) -> None:
        # `checkout -b` onto a branch that already exists fails outright;
        # the operator would get "recovery failed" on a recoverable clone.
        service = _Service(current_branch='master', ahead=0, branch_exists=True)
        self.assertEqual(
            service.recover_clone_onto_task_branch(_repo(), 'UNA-3040'), '',
        )
        self.assertEqual(service.checkouts, [['checkout', 'UNA-3040']])

    def test_a_clone_already_on_the_task_branch_is_untouched(self) -> None:
        service = _Service(current_branch='UNA-3040', ahead=0)
        self.assertEqual(
            service.recover_clone_onto_task_branch(_repo(), 'UNA-3040'), '',
        )
        self.assertEqual(service.git_calls, [])

    def test_commits_on_the_wrong_branch_are_REFUSED_not_moved(self) -> None:
        # They would be left behind by a checkout. Refusing loses nothing;
        # moving them silently could.
        service = _Service(current_branch='master', ahead=3)
        reason = service.recover_clone_onto_task_branch(_repo(), 'UNA-3040')
        self.assertIn('3 commit', reason)
        self.assertIn('rebase', reason)
        self.assertEqual(service.git_calls, [])

    def test_a_failed_checkout_reports_rather_than_pretending(self) -> None:
        service = _Service(current_branch='master', ahead=0,
                           git_error='detached HEAD')
        reason = service.recover_clone_onto_task_branch(_repo(), 'UNA-3040')
        self.assertIn('detached HEAD', reason)

    def test_no_branch_name_is_refused(self) -> None:
        service = _Service(current_branch='master', ahead=0)
        self.assertIn(
            'no task branch name',
            service.recover_clone_onto_task_branch(_repo(), '  '),
        )

    def test_a_missing_clone_is_refused(self) -> None:
        service = _Service(current_branch='master', ahead=0)
        with mock.patch.object(_Service, '_resolve_branch_state',
                               return_value=None):
            reason = service.recover_clone_onto_task_branch(_repo(), 'UNA-3040')
        self.assertIn('missing', reason)
        self.assertEqual(service.git_calls, [])

    def test_an_unreadable_history_is_refused_rather_than_guessed(self) -> None:
        service = _Service(current_branch='master', ahead=0)
        with mock.patch.object(_Service, '_ahead_count',
                               side_effect=RuntimeError('boom')):
            reason = service.recover_clone_onto_task_branch(_repo(), 'UNA-3040')
        self.assertIn('commit history', reason)
        self.assertEqual(service.git_calls, [])


class PrepareTaskBranchIsDestructiveTests(unittest.TestCase):
    """Characterization: why recovery must never use the start-of-task path.

    Not a bug report against ``_make_git_ready_for_work`` — wiping is its
    JOB, and against the fresh clone it was written for that is correct.
    This pins the behaviour so the reason the recovery keeps its distance
    stays visible, and so a future refactor that quietly points recovery
    back at it fails here with the actual commands in the message.
    """

    def _ready_service(self):
        service = _Service(current_branch='master', ahead=0)
        service._current_branch = lambda *a, **k: 'master'
        service._assert_current_branch = lambda *a, **k: None
        service._ensure_clean_worktree = lambda *a, **k: None
        return service

    def test_the_start_of_task_path_wipes_the_working_tree(self) -> None:
        service = self._ready_service()
        repository = SimpleNamespace(
            id='objective_love_core_lib', local_path='/clone',
            remote_url='git@bitbucket.org:x/y.git',
        )
        service._make_git_ready_for_work('/clone', 'master', repository)
        issued = [' '.join(call) for call in service.git_calls]
        self.assertIn('checkout -f master', issued)
        self.assertIn('reset --hard origin/master', issued)
        self.assertIn('clean -fd', issued)

    def test_it_keeps_no_stash_of_what_it_removed(self) -> None:
        # The operator's 41 files were recoverable only because the AGENT
        # had run `git stash` on its own initiative. Nothing in this path
        # saves anything, so that rescue was luck, not design.
        service = self._ready_service()
        repository = SimpleNamespace(
            id='objective_love_core_lib', local_path='/clone',
            remote_url='git@bitbucket.org:x/y.git',
        )
        service._make_git_ready_for_work('/clone', 'master', repository)
        self.assertEqual(
            [call for call in service.git_calls if 'stash' in call], [],
        )


class DestructiveCommandDetectorTests(unittest.TestCase):
    """The guard above is only worth as much as its detector."""

    def test_it_recognises_what_the_wipe_path_actually_ran(self) -> None:
        for argv in (
            ['checkout', '-f', 'master'],
            ['reset', '--hard', 'origin/master'],
            ['clean', '-fd'],
        ):
            self.assertTrue(_is_destructive(argv), argv)

    def test_it_clears_the_commands_the_recovery_is_allowed_to_run(self) -> None:
        for argv in (
            ['checkout', '-b', 'UNA-3040'],
            ['checkout', 'UNA-3040'],
            ['branch', '--list', 'UNA-3040'],
        ):
            self.assertFalse(_is_destructive(argv), argv)


if __name__ == '__main__':
    unittest.main()
