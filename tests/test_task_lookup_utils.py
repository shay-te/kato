import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_core_lib.helpers.task_lookup_utils import (
    find_assigned_or_review_task,
    find_task_by_id,
    task_id_matches,
)


class TaskIdMatchesTests(unittest.TestCase):
    def test_matches_after_normalizing_padding(self) -> None:
        self.assertTrue(task_id_matches(SimpleNamespace(id='  T1 '), 'T1'))

    def test_a_task_without_an_id_matches_nothing(self) -> None:
        self.assertFalse(task_id_matches(SimpleNamespace(), 'T1'))
        self.assertFalse(task_id_matches(SimpleNamespace(id=None), 'T1'))


class FindTaskByIdTests(unittest.TestCase):
    def test_walks_the_queues_in_the_order_given(self) -> None:
        first, second = SimpleNamespace(id='T1'), SimpleNamespace(id='T1')
        service = SimpleNamespace(a=lambda: [first], b=lambda: [second])

        self.assertIs(find_task_by_id(service, 'T1', queues=('a', 'b')), first)
        self.assertIs(find_task_by_id(service, 'T1', queues=('b', 'a')), second)

    def test_reports_a_failing_queue_and_carries_on(self) -> None:
        task = SimpleNamespace(id='T1')
        service = SimpleNamespace(
            boom=MagicMock(side_effect=RuntimeError('fail')),
            ok=lambda: [task],
        )
        errors: list[str] = []

        found = find_task_by_id(
            service, 'T1', queues=('boom', 'ok'), on_error=errors.append,
        )

        self.assertIs(found, task)
        self.assertEqual(errors, ['boom'])

    def test_returns_none_when_nothing_matches(self) -> None:
        service = SimpleNamespace(a=lambda: [SimpleNamespace(id='other')])

        self.assertIsNone(find_task_by_id(service, 'T1', queues=('a',)))


class FindAssignedOrReviewTaskTests(unittest.TestCase):
    """The lookup both ``adopt_task`` and the repositories service run."""

    def test_prefers_the_all_list_over_the_active_queues(self) -> None:
        task = SimpleNamespace(id='T1')
        task_service = MagicMock()
        task_service.list_all_assigned_tasks.return_value = [task]

        self.assertIs(find_assigned_or_review_task(task_service, 'T1'), task)

    def test_falls_through_a_queue_that_raises(self) -> None:
        task = SimpleNamespace(id='T1')
        task_service = MagicMock()
        task_service.list_all_assigned_tasks.side_effect = RuntimeError('fail')
        task_service.get_assigned_tasks.return_value = [task]

        self.assertIs(find_assigned_or_review_task(task_service, 'T1'), task)

    def test_skips_a_queue_attribute_that_is_not_callable(self) -> None:
        task_service = SimpleNamespace(
            list_all_assigned_tasks='not callable',
            get_assigned_tasks=lambda: [SimpleNamespace(id='T1')],
        )

        self.assertEqual(find_assigned_or_review_task(task_service, 'T1').id, 'T1')

    def test_finds_a_task_that_has_already_moved_to_review(self) -> None:
        task = SimpleNamespace(id='T1')
        task_service = MagicMock()
        task_service.list_all_assigned_tasks.return_value = []
        task_service.get_assigned_tasks.return_value = []
        task_service.get_review_tasks.return_value = [task]

        self.assertIs(find_assigned_or_review_task(task_service, 'T1'), task)

    def test_returns_none_when_no_queue_has_it(self) -> None:
        task_service = MagicMock()
        task_service.list_all_assigned_tasks.return_value = []
        task_service.get_assigned_tasks.return_value = []
        task_service.get_review_tasks.return_value = []

        self.assertIsNone(find_assigned_or_review_task(task_service, 'T1'))


class DirectByIdLookupTests(unittest.TestCase):
    """Finding ONE task must not read the operator's whole backlog.

    The queue walk fetches every assigned/review/all task and enriches each
    one (its tags, comments and attachments are separate calls per issue) to
    answer a question about a single id. Measured against a real instance:
    78 issues / 4.78s, against 0.66s for the same task by id. That cost was
    paid on every "Sync repositories" click, which is exactly why syncing an
    existing task felt far slower than the initial clone — the clone path
    already holds the Task and never does this lookup at all.
    """

    def test_a_direct_hit_skips_the_queues_entirely(self) -> None:
        task = SimpleNamespace(id='T1')
        task_service = SimpleNamespace(
            get_task=MagicMock(return_value=task),
            list_all_assigned_tasks=MagicMock(return_value=[task]),
            get_assigned_tasks=MagicMock(return_value=[]),
            get_review_tasks=MagicMock(return_value=[]),
        )

        found = find_assigned_or_review_task(task_service, 'T1')

        self.assertIs(found, task)
        task_service.get_task.assert_called_once_with('T1')
        # The whole point: the backlog is never read.
        task_service.list_all_assigned_tasks.assert_not_called()
        task_service.get_assigned_tasks.assert_not_called()

    def test_a_miss_falls_back_to_the_queues(self) -> None:
        # A platform whose by-id lookup simply does not have it (a task that
        # left the active states, say) must still be found the old way.
        task = SimpleNamespace(id='T1')
        task_service = SimpleNamespace(
            get_task=MagicMock(return_value=None),
            list_all_assigned_tasks=MagicMock(return_value=[task]),
        )

        self.assertIs(find_assigned_or_review_task(task_service, 'T1'), task)
        task_service.list_all_assigned_tasks.assert_called_once_with()

    def test_a_by_id_error_falls_back_instead_of_stranding_the_caller(self) -> None:
        task = SimpleNamespace(id='T1')
        seen = []
        task_service = SimpleNamespace(
            get_task=MagicMock(side_effect=RuntimeError('boom')),
            list_all_assigned_tasks=MagicMock(return_value=[task]),
        )

        found = find_assigned_or_review_task(
            task_service, 'T1', on_error=seen.append,
        )

        self.assertIs(found, task)
        self.assertIn('get_task', seen)

    def test_a_provider_without_get_task_is_unaffected(self) -> None:
        # ``get_task`` is a capability, not a contract — jira/bitbucket/github
        # clients that lack it must behave exactly as before.
        task = SimpleNamespace(id='T1')
        task_service = SimpleNamespace(
            list_all_assigned_tasks=MagicMock(return_value=[task]),
        )
        self.assertIs(find_assigned_or_review_task(task_service, 'T1'), task)

    def test_a_by_id_answer_for_the_WRONG_task_is_rejected(self) -> None:
        # Defensive: a platform that ignores the id and returns something else
        # must not be trusted over the queues.
        wanted = SimpleNamespace(id='T1')
        task_service = SimpleNamespace(
            get_task=MagicMock(return_value=SimpleNamespace(id='OTHER')),
            list_all_assigned_tasks=MagicMock(return_value=[wanted]),
        )
        self.assertIs(find_assigned_or_review_task(task_service, 'T1'), wanted)


if __name__ == '__main__':
    unittest.main()
