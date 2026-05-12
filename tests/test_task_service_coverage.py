"""Coverage for TaskService (lines 79-99, 108, 111)."""

from __future__ import annotations

import types
import unittest
from unittest.mock import MagicMock, Mock

from kato_core_lib.data_layers.service.task_service import TaskService


def _build_service(states=('Open',), data_access=None):
    config = types.SimpleNamespace(
        assignee='me',
        issue_states=list(states),
    )
    if data_access is None:
        data_access = Mock()
        data_access.get_assigned_tasks.return_value = []
    return TaskService(config, data_access)


class TaskServiceListAllAssignedTasksTests(unittest.TestCase):
    """Lines 79-99: ``list_all_assigned_tasks`` unions queue + lifecycle
    states for the planning UI's picker. Locks dedup + ordering."""

    def test_returns_empty_when_no_states_configured(self) -> None:
        # Line 89: ``if not states: return []``.
        config = types.SimpleNamespace(
            assignee='me', issue_states='', progress_state='', review_state='',
            done_state='',
        )
        # parse_issue_states will return [] for ''.
        service = TaskService(config, Mock())
        # Patch _configured_state_value to return '' for every key so
        # the union remains empty.
        service._configured_state_value = lambda _k: ''
        self.assertEqual(service.list_all_assigned_tasks(), [])

    def test_dedups_tasks_by_id_preserving_first_seen(self) -> None:
        # Lines 93-99: dedup by id, first-seen wins.
        task_a = types.SimpleNamespace(id='PROJ-1', summary='first')
        task_a_dup = types.SimpleNamespace(id='PROJ-1', summary='second')
        task_b = types.SimpleNamespace(id='PROJ-2', summary='other')
        data_access = Mock()
        data_access.get_assigned_tasks.return_value = [task_a, task_a_dup, task_b]
        service = _build_service(states=('Open',), data_access=data_access)
        result = service.list_all_assigned_tasks()
        self.assertEqual([t.id for t in result], ['PROJ-1', 'PROJ-2'])
        # First-seen wins (still has summary='first', not 'second').
        self.assertEqual(result[0].summary, 'first')

    def test_skips_tasks_with_blank_id(self) -> None:
        # Line 95: ``if not task_id ...: continue``.
        task_ok = types.SimpleNamespace(id='PROJ-1', summary='x')
        task_blank = types.SimpleNamespace(id='', summary='broken')
        data_access = Mock()
        data_access.get_assigned_tasks.return_value = [task_blank, task_ok]
        service = _build_service(data_access=data_access)
        result = service.list_all_assigned_tasks()
        self.assertEqual([t.id for t in result], ['PROJ-1'])

    def test_unions_progress_review_done_into_states(self) -> None:
        # Lines 79-83: lifecycle states added to the query.
        data_access = Mock()
        data_access.get_assigned_tasks.return_value = []
        config = types.SimpleNamespace(
            assignee='me', issue_states='Open',
            progress_state='In Progress', review_state='In Review',
            done_state='Done',
        )
        service = TaskService(config, data_access)
        service.list_all_assigned_tasks()
        call = data_access.get_assigned_tasks.call_args
        states = call.kwargs['states']
        # Open is the configured queue state; lifecycle states are appended.
        self.assertIn('In Progress', states)
        self.assertIn('In Review', states)
        self.assertIn('Done', states)


class TaskServiceTagDelegationTests(unittest.TestCase):
    def test_add_tag_delegates_to_data_access(self) -> None:
        # Line 108.
        data_access = Mock()
        service = _build_service(data_access=data_access)
        service.add_tag('PROJ-1', 'urgent')
        data_access.add_tag.assert_called_once_with('PROJ-1', 'urgent')

    def test_remove_tag_delegates_to_data_access(self) -> None:
        # Line 111.
        data_access = Mock()
        service = _build_service(data_access=data_access)
        service.remove_tag('PROJ-1', 'urgent')
        data_access.remove_tag.assert_called_once_with('PROJ-1', 'urgent')


if __name__ == '__main__':
    unittest.main()
