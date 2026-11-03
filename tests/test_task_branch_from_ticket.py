"""A ticket can name its branch with ``kato:branch:<name>``.

Anyone who can edit a ticket can add a tag, and the branch is where kato
pushes, so a ticket gets less than a person typing in "New task": a new name
only, before the task starts, and never on a plan's step. A tag that fails is
left out and the task says why, on its id, as it would with no tag.

Real git repositories and real services, as in ``test_local_task_branch_name``.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.plan_queue.step_footer import subtask_description
from kato_core_lib.helpers.kato_tag_utils import build_branch_tag, build_repository_tag
from plan_queue_core_lib.plan_queue_core_lib.ports import LaunchRequest
from tests.test_local_task_branch_name import RealLocalTask, _git

TICKET = 'UNA-77'
BRANCH = 'feature/from-ticket'
PLAN_STEP_DESCRIPTION = subtask_description(LaunchRequest(
    plan_id='a' * 32, step_id='step-1', index=1, count=2, title='Add login',
    description='Do it.', repositories=('api',), source_task_id='UNA-1', plan_title='Login',
), ('api',))


class _Ticket(RealLocalTask, unittest.TestCase):
    def _task(self, *tags, task_id=TICKET, description='Do it.', repositories=('api',)) -> Task:
        return Task(
            id=task_id, summary='Add login', description=description,
            tags=[*map(build_repository_tag, repositories), *tags],
        )

    def _start(self, *tags, **kwargs) -> Path:
        task = self._task(*tags, **kwargs)
        self.planning.resolve_planning_context(task)
        return Path(self.workspace.repository_path(str(task.id), 'api'))

    def _says(self, task_id: str = TICKET) -> str:
        log = self.workspace.preflight_log_path(task_id)
        return log.read_text(encoding='utf-8') if log.is_file() else ''

    def assert_stayed_on_the_ticket_id(self, clone: Path, *reason: str) -> None:
        self.assertEqual(self.repositories.task_branch_name(TICKET), TICKET)
        self.assertEqual(_git(clone, 'branch', '--show-current').strip(), TICKET)
        for words in reason:
            self.assertIn(words, self._says())


class TicketNamesItsBranchTests(_Ticket):
    def test_the_clone_is_on_the_branch_the_ticket_names(self) -> None:
        clone = self._start(build_branch_tag(BRANCH))
        self.assertEqual(_git(clone, 'branch', '--show-current').strip(), BRANCH)
        self.assertEqual(self.repositories.task_branch_name(TICKET), BRANCH)

    def test_the_task_id_gets_no_branch_alongside_it(self) -> None:
        clone = self._start(build_branch_tag(BRANCH))
        self.assertNotIn(TICKET, self._branches(clone))

    def test_with_no_tag_the_branch_is_the_ticket_id(self) -> None:
        self.assert_stayed_on_the_ticket_id(self._start())

    def test_a_later_pass_keeps_the_name(self) -> None:
        self._start(build_branch_tag(BRANCH))
        self.planning.resolve_planning_context(self._task(build_branch_tag(BRANCH)))
        self.assertEqual(self.repositories.task_branch_name(TICKET), BRANCH)

    def test_a_tag_removed_later_changes_nothing(self) -> None:
        self._start(build_branch_tag(BRANCH))
        self.planning.resolve_planning_context(self._task())
        self.assertEqual(self.repositories.task_branch_name(TICKET), BRANCH)

    def test_a_clone_that_failed_first_time_still_gets_the_name(self) -> None:
        real_clone = self.repositories.ensure_clone
        with mock.patch.object(
            self.repositories, 'ensure_clone', side_effect=RuntimeError('network'),
        ):
            self._start(build_branch_tag(BRANCH))
        self.repositories.ensure_clone = real_clone
        clone = self._start(build_branch_tag(BRANCH))
        self.assertEqual(_git(clone, 'branch', '--show-current').strip(), BRANCH)


class TicketGetsLessThanAPersonTests(_Ticket):
    def test_a_branch_people_already_use_is_not_taken(self) -> None:
        _git(self.seed, 'checkout', '-q', '-b', 'release/1.0')
        _git(self.seed, 'push', '-q', 'origin', 'release/1.0')
        self.assert_stayed_on_the_ticket_id(
            self._start(build_branch_tag('release/1.0')), 'can only name a new branch',
        )

    def test_a_remote_that_cannot_be_asked_is_not_a_yes(self) -> None:
        with mock.patch.object(
            self.repositories, 'remote_has_branch', side_effect=RuntimeError('offline'),
        ):
            clone = self._start(build_branch_tag(BRANCH))
        self.assert_stayed_on_the_ticket_id(clone, 'could not ask the remote of api', 'offline')

    def test_the_branch_it_merges_into_is_refused(self) -> None:
        self.assert_stayed_on_the_ticket_id(
            self._start(build_branch_tag('master')), 'merges into',
        )

    def test_a_name_git_cannot_take_is_refused(self) -> None:
        self.assert_stayed_on_the_ticket_id(
            self._start(build_branch_tag('-x;rm')), 'cannot be a branch name',
        )

    def test_a_branch_another_task_works_on_is_refused(self) -> None:
        self._prepared('feature/taken')
        self.assert_stayed_on_the_ticket_id(
            self._start(build_branch_tag('feature/taken')), 'already works on',
        )

    def test_a_plan_step_stays_on_its_ticket_id(self) -> None:
        self.assert_stayed_on_the_ticket_id(
            self._start(build_branch_tag(BRANCH), description=PLAN_STEP_DESCRIPTION),
            'how a plan finds its pull request',
        )

    def test_a_task_already_started_stays_where_it_is(self) -> None:
        clone = self._start()
        self._add_repository('web')
        self.planning.resolve_planning_context(
            self._task(build_branch_tag(BRANCH), repositories=('api', 'web')),
        )
        self.assert_stayed_on_the_ticket_id(clone, 'already started')

    def test_a_tag_changed_later_does_not_move_the_task(self) -> None:
        clone = self._start(build_branch_tag(BRANCH))
        self._add_repository('web')
        self.planning.resolve_planning_context(
            self._task(build_branch_tag('feature/other'), repositories=('api', 'web')),
        )
        self.assertEqual(self.repositories.task_branch_name(TICKET), BRANCH)
        self.assertEqual(_git(clone, 'branch', '--show-current').strip(), BRANCH)
        self.assertIn(f'already works on {BRANCH}', self._says())

    def test_a_repository_added_to_a_named_task_says_nothing(self) -> None:
        self._start(build_branch_tag(BRANCH))
        self._add_repository('web')
        self.planning.resolve_planning_context(
            self._task(build_branch_tag(BRANCH), repositories=('api', 'web')),
        )
        self.assertNotIn('not used', self._says())

    def test_a_branch_that_only_ends_the_same_is_a_new_one(self) -> None:
        _git(self.seed, 'push', '-q', 'origin', f'HEAD:refs/heads/other/{BRANCH}')
        clone = self._start(build_branch_tag(BRANCH))
        self.assertEqual(_git(clone, 'branch', '--show-current').strip(), BRANCH)

    def test_a_pass_that_clones_nothing_asks_no_one(self) -> None:
        self._start()
        with mock.patch.object(self.repositories, 'remote_has_branch') as asked:
            self.planning.resolve_planning_context(self._task(build_branch_tag(BRANCH)))
        asked.assert_not_called()
        self.assertNotIn('not used', self._says())


if __name__ == '__main__':
    unittest.main()
