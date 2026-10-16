"""Adding a repo from kato tags the task in KATO first, and the ticket best-effort.

Operator report (UNA-3264): "+ Add repository" failed with
``400 Client Error: Bad Request for url: .../api/issues/UNA-3264/tags``.
YouTrack attaches a tag by its database id (the client sent a name), and the
per-repo tag did not exist there yet — so the very first add of any repo was
refused, failing a flow that never needed the tracker.

The tag is now recorded in the workspace's own metadata (the source of truth),
repo resolution reads it next to the tracker's tags, and the SAME tag is then put
on the ticket as a best-effort mirror: whatever the tracker answers — refusal,
silence, "no tags here" — is reported, and never fails the add.

Real WorkspaceService writing real files, real RepositoryInventoryService;
the tracker is a stub that answers exactly what YouTrack answered.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib.data_layers.service.repository_inventory_service import (
    RepositoryInventoryService,
)
from kato_core_lib.data_layers.service.task_repository_service import (
    TaskRepositoryService,
)
from kato_core_lib.kato_core_lib import KatoCoreLib
from tests.chaos_lib import build_real_workspace_service, materialize_workspace
from tests.utils import build_task


def _repo(repo_id: str):
    return SimpleNamespace(
        id=repo_id, display_name=repo_id, repo_slug=repo_id, aliases=[],
        local_path=f'/src/{repo_id}', provider_base_url='https://example.test',
    )


class AddRepositoryTagStaysInKatoTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-add-repo-tag-')
        self.addCleanup(self._tmp.cleanup)
        self.task_id = 'UNA-3264'
        self.workspaces = build_real_workspace_service(Path(self._tmp.name))
        materialize_workspace(self.workspaces, self.task_id, repository_ids=['alpha'])
        self.inventory = RepositoryInventoryService([_repo('alpha'), _repo('funnel-core-lib')])
        self.inventory.attach_kato_task_tags(
            lambda task_id: KatoCoreLib._kato_tags_for_task(
                SimpleNamespace(workspace_manager=self.workspaces), task_id,
            ),
        )
        # What YouTrack really answered to the POST.
        self.tracker = MagicMock()
        self.tracker.add_tag.side_effect = RuntimeError(
            '400 Client Error: Bad Request for url: '
            'https://tracker.example/api/issues/UNA-3264/tags',
        )
        self.service = TaskRepositoryService(
            repository_service=self.inventory,
            task_service=self.tracker,
            workspace_manager=self.workspaces,
            logger=MagicMock(),
        )

    def _add(self, repo_id: str = 'funnel-core-lib') -> dict:
        with patch.object(
            self.service, 'sync_task_repositories', return_value={'synced': True},
        ):
            return self.service.add_task_repository(self.task_id, repo_id)

    def _tags_on_disk(self) -> list[str]:
        meta = Path(self.workspaces.workspace_path(self.task_id)) / '.workspace-meta.json'
        return json.loads(meta.read_text())['tags']

    def test_add_succeeds_though_the_tracker_refuses_the_tag(self) -> None:
        result = self._add()

        self.assertTrue(result['added'], result)
        self.assertTrue(result['tag_added'])
        self.assertEqual(result['tag_name'], 'kato:repo:funnel-core-lib')
        # The ticket WAS tried — and the tracker's own words come back, so the
        # operator sees why it is not tagged instead of a bare failure.
        self.tracker.add_tag.assert_called_once_with('UNA-3264', 'kato:repo:funnel-core-lib')
        self.assertEqual(result['ticket_tag']['status'], 'failed')
        self.assertIn('400 Client Error', result['ticket_tag']['error'])

    def test_a_refused_ticket_tag_still_leaves_the_tag_in_kato_and_the_repo_resolvable(self) -> None:
        self._add()

        self.assertEqual(self._tags_on_disk(), ['kato:repo:funnel-core-lib'])
        task = build_task(task_id=self.task_id, tags=['unrelated'])
        self.assertEqual(
            [r.id for r in self.inventory.resolve_task_repositories(task)], ['funnel-core-lib'],
        )

    def test_the_ticket_is_tagged_too_when_the_tracker_accepts_it(self) -> None:
        self.tracker.add_tag.side_effect = None

        result = self._add()

        self.tracker.add_tag.assert_called_once_with('UNA-3264', 'kato:repo:funnel-core-lib')
        self.assertEqual(result['ticket_tag'], {'status': 'added', 'error': ''})
        self.assertTrue(result['tag_added'])

    def test_a_tracker_without_tags_is_skipped_not_attempted(self) -> None:
        # Bitbucket issues have no tags; "adding" one there overwrites the
        # component somebody set by hand.
        self.tracker.supports_tags.return_value = False

        result = self._add()

        self.tracker.add_tag.assert_not_called()
        self.assertEqual(result['ticket_tag']['status'], 'unsupported')
        self.assertTrue(result['added'])

    def test_when_the_capability_check_itself_fails_the_tracker_is_still_tried(self) -> None:
        self.tracker.supports_tags.side_effect = RuntimeError('cannot tell')
        self.tracker.add_tag.side_effect = None

        self.assertEqual(self._add()['ticket_tag']['status'], 'added')

    def test_an_error_with_no_message_is_still_named(self) -> None:
        self.tracker.add_tag.side_effect = ValueError()

        self.assertEqual(self._add()['ticket_tag']['error'], 'ValueError')

    def test_a_tracker_that_never_answers_cannot_hold_the_add_open(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)
        self.tracker.add_tag.side_effect = lambda *args: release.wait(5)

        with patch(
            'kato_core_lib.data_layers.service.task_repository_service._TICKET_TAG_DEADLINE_SECONDS',
            0.05,
        ):
            result = self._add()

        self.assertTrue(result['added'], result)
        self.assertEqual(result['ticket_tag']['status'], 'failed')
        self.assertIn('did not answer', result['ticket_tag']['error'])
        self.assertEqual(self._tags_on_disk(), ['kato:repo:funnel-core-lib'])

    def test_adding_again_retries_the_ticket(self) -> None:
        first = self._add()
        self.tracker.add_tag.side_effect = None
        second = self._add()

        self.assertEqual(first['ticket_tag']['status'], 'failed')
        self.assertEqual(second['ticket_tag']['status'], 'added')
        self.assertFalse(second['tag_added'])  # kato already had it; only the ticket was retried

    def test_the_tag_is_written_to_the_workspace_metadata_on_disk(self) -> None:
        self._add()

        self.assertEqual(self._tags_on_disk(), ['kato:repo:funnel-core-lib'])

    def test_adding_the_same_repo_twice_records_one_tag(self) -> None:
        first = self._add()
        second = self._add()

        self.assertTrue(first['tag_added'])
        self.assertFalse(second['tag_added'])
        self.assertEqual(self._tags_on_disk(), ['kato:repo:funnel-core-lib'])

    def test_resolution_sees_the_repo_with_no_tag_on_the_tracker(self) -> None:
        self._add()
        # The tracker's copy of the task carries no repo tag whatsoever.
        task = build_task(task_id=self.task_id, tags=['unrelated'])

        resolved = self.inventory.resolve_task_repositories(task)

        self.assertEqual([r.id for r in resolved], ['funnel-core-lib'])

    def test_tags_survive_a_later_provisioning_pass(self) -> None:
        self._add()
        # ``provision_task_workspace_clones`` calls ``create`` on every sync.
        self.workspaces.create(task_id=self.task_id, repository_ids=['alpha', 'funnel-core-lib'])

        self.assertEqual(self._tags_on_disk(), ['kato:repo:funnel-core-lib'])

    def test_a_task_with_no_workspace_fails_before_anything_is_cloned(self) -> None:
        with patch.object(self.service, 'sync_task_repositories') as sync:
            result = self.service.add_task_repository('GHOST-1', 'funnel-core-lib')

        self.assertFalse(result['added'])
        self.assertIn('failed to tag task', result['error'])
        sync.assert_not_called()
        self.tracker.add_tag.assert_not_called()

    def test_kato_tags_for_a_task_without_a_workspace_manager_are_empty(self) -> None:
        self.assertEqual(
            KatoCoreLib._kato_tags_for_task(SimpleNamespace(workspace_manager=None), 'T-1'),
            [],
        )
        self.assertEqual(
            KatoCoreLib._kato_tags_for_task(
                SimpleNamespace(workspace_manager=self.workspaces), 'NO-SUCH-1',
            ),
            [],
        )


if __name__ == '__main__':
    unittest.main()
