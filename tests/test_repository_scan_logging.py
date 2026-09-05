"""The repository scan must narrate itself into the activity log.

Every failure path in ``sync_task_repositories`` / ``add_task_repository``
used to return its reason in the response envelope ONLY. The operator's
sole way to read it was to hover the UI toast, so a failure that scrolled
past — a repo sitting under the ignored-folders list, a repo missing from
the inventory — left nothing to search for in the orchestrator activity
log. These tests pin the log lines, not the envelopes.

Real ``TaskRepositoryService`` against stub collaborators; the only patch
is the clone primitive, so the failure-aggregation path runs for real.
"""

from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from kato_core_lib.data_layers.service.task_repository_service import (
    TaskRepositoryService,
)


def _repo(repo_id):
    return SimpleNamespace(id=repo_id, name=repo_id, url=f'ssh://git@h/{repo_id}.git')


class _CapturingLogger(logging.Logger):
    """A real Logger that keeps every formatted record."""

    def __init__(self) -> None:
        super().__init__('test-repo-scan', logging.DEBUG)
        self.records: list[tuple[str, str]] = []

    def handle(self, record):  # noqa: D102 - Logger override
        self.records.append((record.levelname, record.getMessage()))

    def lines(self, level=None):
        return [m for lvl, m in self.records if level is None or lvl == level]

    def text(self, level=None):
        return '\n'.join(self.lines(level))


class RepositoryScanLoggingTests(unittest.TestCase):
    """The scan announces itself and every failure reaches the log."""

    def setUp(self) -> None:
        self.logger = _CapturingLogger()
        self.task = SimpleNamespace(id='PROJ-1', tags=[], description='')
        self.workspace = SimpleNamespace(repository_ids=['client'])
        self.workspace_manager = Mock()
        self.workspace_manager.get.return_value = self.workspace
        self.repository_service = Mock()
        self.repository_service.repositories = [_repo('client'), _repo('server')]
        self.repository_service.resolve_task_repositories.return_value = [
            _repo('client'),
        ]
        self.repository_service.build_branch_name.return_value = 'kato/PROJ-1'
        # Real contract: '' when the clone is already on its task branch. A
        # bare Mock returns a truthy Mock, which sync correctly reads as
        # "this repo is stranded on the default branch".
        self.repository_service.recover_clone_onto_task_branch.return_value = ''
        self.task_service = Mock()
        self.service = TaskRepositoryService(
            repository_service=self.repository_service,
            task_service=self.task_service,
            workspace_manager=self.workspace_manager,
            logger=self.logger,
        )
        self.service._lookup_task_for_sync = Mock(return_value=self.task)

    # ---- the scan announces itself -------------------------------------

    def test_sync_announces_the_scan(self) -> None:
        self.service.sync_task_repositories('PROJ-1')
        self.assertIn(
            'scanning task PROJ-1 for repositories', self.logger.text('INFO'),
        )

    def test_sync_reports_what_the_scan_found(self) -> None:
        self.service.sync_task_repositories('PROJ-1')
        self.assertIn(
            'repository scan for task PROJ-1 found 1 repository '
            '(1 already in the workspace, 0 to add)',
            self.logger.text('INFO'),
        )

    def test_scan_line_names_the_repositories_it_will_add(self) -> None:
        self.repository_service.resolve_task_repositories.return_value = [
            _repo('client'), _repo('server'),
        ]
        with patch(
            'kato_core_lib.data_layers.service.workspace_provisioning_service'
            '.provision_task_workspace_clones',
            return_value=[_repo('client'), _repo('server')],
        ):
            self.service.sync_task_repositories('PROJ-1')
        found = self.logger.text('INFO')
        self.assertIn('found 2 repositories', found)
        self.assertIn('1 to add: server', found)

    def test_successful_add_is_logged(self) -> None:
        self.repository_service.resolve_task_repositories.return_value = [
            _repo('client'), _repo('server'),
        ]
        with patch(
            'kato_core_lib.data_layers.service.workspace_provisioning_service'
            '.provision_task_workspace_clones',
            return_value=[_repo('client'), _repo('server')],
        ):
            self.service.sync_task_repositories('PROJ-1')
        self.assertIn('added server to task PROJ-1', self.logger.text('INFO'))

    # ---- failures are findable in the log -------------------------------

    def test_ignored_folder_rejection_reaches_the_log(self) -> None:
        """The operator's real symptom: a repo under the ignored list."""
        self.repository_service.resolve_task_repositories.side_effect = ValueError(
            'task PROJ-1 references repositories that are in '
            'AGENT_IGNORED_REPOSITORY_FOLDERS: objective_love_core_lib',
        )
        result = self.service.sync_task_repositories('PROJ-1')
        self.assertFalse(result['synced'])
        logged = self.logger.text('ERROR')
        self.assertIn('repository scan for task PROJ-1 failed', logged)
        self.assertIn('objective_love_core_lib', logged)

    def test_missing_workspace_reaches_the_log(self) -> None:
        self.workspace_manager.get.return_value = None
        self.service.sync_task_repositories('PROJ-1')
        self.assertIn(
            'no workspace exists for this task yet', self.logger.text('ERROR'),
        )

    def test_missing_task_reaches_the_log(self) -> None:
        self.service._lookup_task_for_sync = Mock(return_value=None)
        self.service.sync_task_repositories('PROJ-1')
        self.assertIn(
            'could not find PROJ-1 on the ticket platform',
            self.logger.text('ERROR'),
        )

    def test_clone_failure_is_logged_per_repository(self) -> None:
        self.repository_service.resolve_task_repositories.return_value = [
            _repo('client'), _repo('server'),
        ]
        with patch(
            'kato_core_lib.data_layers.service.workspace_provisioning_service'
            '.provision_task_workspace_clones',
            side_effect=RuntimeError('permission denied (publickey)'),
        ):
            result = self.service.sync_task_repositories('PROJ-1')
        self.assertFalse(result['synced'])
        logged = self.logger.text('ERROR')
        self.assertIn('adding repository server to task PROJ-1 failed', logged)
        self.assertIn('permission denied (publickey)', logged)

    # ---- add_task_repository --------------------------------------------

    def test_add_announces_itself(self) -> None:
        with patch(
            'kato_core_lib.data_layers.service.workspace_provisioning_service'
            '.provision_task_workspace_clones',
            return_value=[_repo('client'), _repo('server')],
        ):
            self.repository_service.resolve_task_repositories.return_value = [
                _repo('client'), _repo('server'),
            ]
            self.service.add_task_repository('PROJ-1', 'server')
        self.assertIn(
            'adding repository server to task PROJ-1', self.logger.text('INFO'),
        )

    def test_add_of_unknown_repository_names_the_known_ones(self) -> None:
        result = self.service.add_task_repository('PROJ-1', 'nope')
        self.assertFalse(result['added'])
        logged = self.logger.text('ERROR')
        self.assertIn(
            "add repository nope to task PROJ-1 failed: not in the kato "
            "inventory", logged,
        )
        self.assertIn('client, server', logged)

    def test_add_that_does_not_complete_logs_the_reason(self) -> None:
        self.repository_service.resolve_task_repositories.side_effect = ValueError(
            'boom',
        )
        result = self.service.add_task_repository('PROJ-1', 'server')
        # The tag landed, so ``added`` stays True — but the clone did not,
        # and THAT is the part the operator needs to find in the log.
        self.assertFalse(result['sync']['synced'])
        self.assertIn(
            'add repository server to task PROJ-1 did not complete',
            self.logger.text('ERROR'),
        )

    def test_add_failure_detail_lists_each_failed_repository(self) -> None:
        described = TaskRepositoryService._describe_repository_failures({
            'failed_repositories': [
                {'repository_id': 'a', 'error': 'x'},
                {'repository_id': '', 'error': ''},
            ],
        })
        self.assertEqual(described, 'a: x; <unknown>: unknown error')


if __name__ == '__main__':
    unittest.main()
