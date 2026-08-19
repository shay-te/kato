"""Junk in the log, and work on tasks the operator already deleted.

Both come from the same habit: reporting a STEADY STATE as if it were an
event. A missing optional scanner, a workspace that is already done, and
git's per-commit rebase counter are all facts that do not change — logged
per scan they crowd out the lines that do matter, and a log nobody reads
is the same as no log.

The forgotten-task half is worse than noise: a task the operator deleted
was still being picked up by the cleanup sweep and narrated every scan.
"""

from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest import mock

from git_core_lib.git_core_lib.client.git_client import GitClientMixin


class GitOutputCondensingTests(unittest.TestCase):
    def test_rebase_progress_is_dropped_but_the_error_survives(self) -> None:
        noisy = '\n'.join(
            [f'Rebasing ({i}/261)' for i in range(1, 71)]
            + ['error: could not apply 946752e0b... Implement UNA-2800',
               'hint: Resolve all conflicts manually, mark them as resolved with',
               'Could not apply 946752e0b... # Implement UNA-2800'],
        )
        out = GitClientMixin._condense_git_output(noisy)
        self.assertNotIn('Rebasing (', out)
        self.assertIn('could not apply', out)
        self.assertIn('hint:', out)

    def test_long_output_is_capped_and_says_so(self) -> None:
        out = GitClientMixin._condense_git_output(
            '\n'.join(f'error: line {i}' for i in range(100)),
        )
        self.assertLessEqual(len(out.splitlines()), GitClientMixin._MAX_DETAIL_LINES + 1)
        self.assertIn('omitted', out)

    def test_a_short_real_error_is_untouched(self) -> None:
        self.assertEqual(
            GitClientMixin._condense_git_output('fatal: not a git repository'),
            'fatal: not a git repository',
        )

    def test_progress_only_output_still_yields_something(self) -> None:
        # Never return an empty reason — "it failed and here is nothing"
        # is the least useful message possible.
        self.assertTrue(GitClientMixin._condense_git_output('Rebasing (1/2)\nRebasing (2/2)'))


class ScannerUnavailableIsSaidOnceTests(unittest.TestCase):
    def test_repeated_scans_log_the_missing_tool_once(self) -> None:
        import tempfile
        from security_scanner_core_lib.security_scanner_core_lib.security_scanner_service import (
            SecurityScannerService,
        )
        logger = mock.MagicMock(spec=logging.Logger)
        service = SecurityScannerService(logger=logger)
        with tempfile.TemporaryDirectory() as workspace:
            for _ in range(4):
                service.scan_workspace(workspace)
        unavailable = [
            call for call in logger.info.call_args_list
            if 'unavailable' in str(call.args[0])
        ]
        names = {call.args[1] for call in unavailable}
        self.assertEqual(
            len(unavailable), len(names),
            'a missing optional scanner was logged more than once per runner',
        )


class ForgottenTasksAreLeftAloneTests(unittest.TestCase):
    """A deleted task must not be re-processed OR narrated."""

    def _service(self, stale_ids, forgotten, current_status):
        from kato_core_lib.data_layers.service import agent_service as module
        service = object.__new__(module.AgentService)
        service.logger = mock.MagicMock(spec=logging.Logger)
        service._workspace_manager = mock.MagicMock()
        service._workspace_manager.get.return_value = SimpleNamespace(
            status=current_status,
        )
        service._session_manager = mock.MagicMock()
        service._task_service = mock.MagicMock()
        service._task_service.get_assigned_tasks.return_value = []
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
