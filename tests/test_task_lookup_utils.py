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


if __name__ == '__main__':
    unittest.main()
