"""A repo tagged onto a task mid-flight must reach ``.kato-meta.json``.

The ticket's ``kato:repo:<id>`` tags are the durable statement of which
repositories a task touches. The workspace metadata file is what every
publish path actually reads (``_resolve_publish_context``). When the two
drift — the agent asks for one more repo and the operator tags it while
the task is already running — the newcomer gets cloned and edited and
then silently skipped by push / PR / Update source, with no error
anywhere to say so.

These tests pin the reconcile that keeps them together: it runs on the
scan tick (task object already in hand, so no extra ticket call) and
again before each operator publish action.

Real WorkspaceService writing real files; only the upstream services
that would need HTTP credentials are mocked.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.chaos_lib import build_real_agent_service, materialize_workspace


def _repo(repo_id: str):
    return SimpleNamespace(id=repo_id, local_path=f'/src/{repo_id}')


class TaskRepositoryTagReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-tag-reconcile-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.task_id = 'PROJ-7'
        self.service, self.workspace_service = build_real_agent_service(self.root)
        materialize_workspace(
            self.workspace_service, self.task_id, repository_ids=['repo-a'],
        )
        # The ticket now names a second repo — the one the operator added
        # mid-task after the agent asked for it.
        self.service._repository_service.resolve_task_repositories.return_value = [
            _repo('repo-a'), _repo('repo-b'),
        ]

    def _metadata_on_disk(self) -> dict:
        path = self.workspace_service.workspace_path(self.task_id)
        return json.loads(
            (Path(path) / '.workspace-meta.json').read_text(encoding='utf-8'),
        )

    def test_reconcile_writes_the_new_repo_into_the_metadata_file(self) -> None:
        task = SimpleNamespace(id=self.task_id, summary='s', description='')
        result = self.service.reconcile_task_repositories(self.task_id, task=task)
        self.assertEqual(result.get('added_repositories'), ['repo-b'])
        self.assertEqual(
            self._metadata_on_disk()['repository_ids'], ['repo-a', 'repo-b'],
        )

    def test_reconcile_puts_the_new_clone_on_the_task_branch(self) -> None:
        # Metadata alone is not enough: a clone left on master has nothing
        # committed to the task branch, so push skips it just as quietly.
        task = SimpleNamespace(id=self.task_id, summary='s', description='')
        self.service.reconcile_task_repositories(self.task_id, task=task)
        self.service._repository_service.prepare_task_branches.assert_called_once()
        prepared, branches = (
            self.service._repository_service.prepare_task_branches.call_args[0]
        )
        self.assertEqual([r.id for r in prepared], ['repo-b'])
        self.assertIn('repo-b', branches)

    def test_reconcile_costs_no_ticket_call_when_given_the_task(self) -> None:
        # The scan loop runs this for every assigned task on every tick;
        # a per-task lookup there is exactly the provider load the 180s
        # cadence exists to avoid.
        task = SimpleNamespace(id=self.task_id, summary='s', description='')
        with patch.object(self.service, '_lookup_task_for_sync') as lookup:
            self.service.reconcile_task_repositories(self.task_id, task=task)
        lookup.assert_not_called()

    def test_reconcile_looks_the_task_up_when_not_given_one(self) -> None:
        task = SimpleNamespace(id=self.task_id, summary='s', description='')
        with patch.object(self.service, '_lookup_task_for_sync',
                          return_value=task) as lookup:
            self.service.reconcile_task_repositories(self.task_id)
        lookup.assert_called_once_with(self.task_id)

    def test_unchanged_tags_leave_the_metadata_alone(self) -> None:
        self.service._repository_service.resolve_task_repositories.return_value = [
            _repo('repo-a'),
        ]
        task = SimpleNamespace(id=self.task_id, summary='s', description='')
        result = self.service.reconcile_task_repositories(self.task_id, task=task)
        self.assertEqual(result.get('added_repositories'), [])
        self.assertEqual(self._metadata_on_disk()['repository_ids'], ['repo-a'])

    def test_a_task_with_no_workspace_is_a_no_op(self) -> None:
        task = SimpleNamespace(id='PROJ-NONE', summary='s', description='')
        with patch.object(self.service, 'sync_task_repositories') as sync:
            result = self.service.reconcile_task_repositories('PROJ-NONE', task=task)
        self.assertEqual(result, {})
        sync.assert_not_called()

    def test_the_scan_tick_reconciles_before_its_short_circuits(self) -> None:
        # A live chat task never reaches preflight — the wait-planning
        # short-circuit returns first — so this is the only place the scan
        # can notice a newly-tagged repo.
        task = SimpleNamespace(id=self.task_id, summary='s', description='')
        self.service._task_preflight_service = MagicMock()
        self.service._task_preflight_service.prepare_task_execution_context.return_value = None
        with patch.object(self.service, '_lookup_task_for_sync') as lookup:
            self.service.process_assigned_task(task)
        lookup.assert_not_called()
        self.assertEqual(
            self._metadata_on_disk()['repository_ids'], ['repo-a', 'repo-b'],
        )


if __name__ == '__main__':
    unittest.main()
