"""Work on tasks the operator already deleted.

A forgotten task must not be re-processed OR narrated: kato kept logging
"task X is no longer assigned" every scan tick for tasks that had been
deleted, which reads as kato still working on them.

The log-condensing and scanner-logging tests that used to live here moved
into the libs that own that code (git_core_lib / security_scanner_core_lib)
— a lib's tests belong inside the lib.
"""

from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest import mock


class ForgottenTasksAreLeftAloneTests(unittest.TestCase):
    """A deleted task must not be re-processed OR narrated."""

    def _service(self, stale_ids, forgotten, current_status):
        from kato_core_lib.data_layers.service import task_cleanup_service as module
        workspace_manager = mock.MagicMock()
        workspace_manager.get.return_value = SimpleNamespace(status=current_status)
        task_service = mock.MagicMock()
        task_service.get_assigned_tasks.return_value = []
        service = module.TaskCleanupService(
            task_service=task_service,
            session_manager=mock.MagicMock(),
            workspace_manager=workspace_manager,
            logger=mock.MagicMock(spec=logging.Logger),
        )
        service._stale_planning_task_ids = lambda _live: set(stale_ids)
        return service, module

    def test_a_forgotten_task_is_neither_touched_nor_logged(self) -> None:
        service, module = self._service({'UNA-1'}, {'UNA-1'}, 'active')
        with mock.patch.object(
            module, 'forgotten_task_ids', create=True, return_value={'UNA-1'},
        ), mock.patch(
            'kato_core_lib.helpers.forgotten_tasks_store.forgotten_task_ids',
            return_value={'UNA-1'},
        ):
            service._cleanup_done_planning_sessions(set())
        service._workspace_manager.update_status.assert_not_called()
        self.assertFalse(service.logger.info.called)

    def test_an_already_done_workspace_is_not_re_announced(self) -> None:
        # The fifteen identical lines every three minutes.
        service, _module = self._service({'UNA-2'}, set(), 'done')
        with mock.patch(
            'kato_core_lib.helpers.forgotten_tasks_store.forgotten_task_ids',
            return_value=set(),
        ):
            service._cleanup_done_planning_sessions(set())
        service._workspace_manager.update_status.assert_not_called()
        self.assertFalse(service.logger.info.called)

    def test_a_genuine_transition_IS_logged_once(self) -> None:
        service, _module = self._service({'UNA-3'}, set(), 'active')
        with mock.patch(
            'kato_core_lib.helpers.forgotten_tasks_store.forgotten_task_ids',
            return_value=set(),
        ):
            service._cleanup_done_planning_sessions(set())
        service._workspace_manager.update_status.assert_called_once()
        self.assertTrue(service.logger.info.called)


if __name__ == '__main__':
    unittest.main()
