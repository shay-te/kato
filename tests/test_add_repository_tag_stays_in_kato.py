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
        self.inventory = RepositoryInventoryService(
            [_repo('alpha'), _repo('funnel-core-lib'), _repo('beta'), _repo('gamma')],
        )
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

    # ----- an add is only an add when the repository ends up on the task -----

    def _add_with_sync(self, sync_result: dict, repo_id: str = 'funnel-core-lib') -> dict:
        with patch.object(self.service, 'sync_task_repositories', return_value=sync_result):
            return self.service.add_task_repository(self.task_id, repo_id)

    def test_a_sync_that_never_names_the_repository_is_reported_not_called_added(self) -> None:
        # UNA-3272 + event-core-lib: tagged, "synced", nothing cloned — and the toast
        # said "repository was already cloned".
        result = self._add_with_sync({
            'synced': True, 'added_repositories': [], 'already_present': ['alpha'],
            'failed_repositories': [],
        })

        self.assertFalse(result['sync']['synced'])
        [failure] = result['sync']['failed_repositories']
        self.assertEqual(failure['repository_id'], 'funnel-core-lib')
        self.assertIn('does not include it', failure['error'])
        self.service.logger.error.assert_called()

    def test_a_repository_that_was_cloned_or_already_there_is_an_add(self) -> None:
        cloned = self._add_with_sync({
            'synced': True, 'added_repositories': ['funnel-core-lib'], 'already_present': [],
        })
        self.assertTrue(cloned['sync']['synced'])
        there = self._add_with_sync({
            'synced': True, 'added_repositories': [], 'already_present': ['Funnel-Core-Lib'],
        })
        self.assertTrue(there['sync']['synced'])

    def test_a_sync_that_already_failed_is_left_as_it_reported(self) -> None:
        failed = {'synced': False, 'failed_repositories': [{'repository_id': 'x', 'error': 'boom'}]}
        self.assertEqual(self._add_with_sync(failed)['sync'], failed)

    def test_a_plan_sub_task_really_gets_the_repository_it_was_given(self) -> None:
        # End to end for the real bug: the footer named three repositories, the
        # operator added a fourth, and resolution must now include it.
        from tests.test_plan_queue_kato_hooks import footer

        self._add()
        sub_task = build_task(task_id=self.task_id, description=footer(('alpha',)))
        self.assertEqual(
            [r.id for r in self.inventory.resolve_task_repositories(sub_task)],
            ['alpha', 'funnel-core-lib'],
        )

    # ----- several repositories at once -----

    def _add_many(self, repo_ids, sync_result=None) -> dict:
        sync = sync_result if sync_result is not None else {
            'synced': True, 'added_repositories': list(repo_ids), 'already_present': ['alpha'],
        }
        with patch.object(self.service, 'sync_task_repositories', return_value=sync) as patched:
            result = self.service.add_task_repositories(self.task_id, repo_ids)
        self.sync = patched
        return result

    def test_several_repositories_are_tagged_one_by_one_and_cloned_in_one_sync(self) -> None:
        self.tracker.add_tag.side_effect = None

        result = self._add_many(['beta', 'gamma'])

        # ONE sync for both: they clone together, not one scan and clone per pick.
        self.sync.assert_called_once_with(self.task_id)
        self.assertEqual([r['repository_id'] for r in result['repositories']], ['beta', 'gamma'])
        self.assertEqual([r['tag_name'] for r in result['repositories']],
                         ['kato:repo:beta', 'kato:repo:gamma'])
        self.assertEqual(self._tags_on_disk(), ['kato:repo:beta', 'kato:repo:gamma'])
        self.assertEqual(
            sorted(call.args[1] for call in self.tracker.add_tag.call_args_list),
            ['kato:repo:beta', 'kato:repo:gamma'],
        )
        self.assertTrue(result['added'])
        self.assertEqual(result['rejected'], [])
        self.assertTrue(all(r['ticket_tag']['status'] == 'added' for r in result['repositories']))

    def test_one_the_inventory_does_not_know_is_rejected_and_the_rest_still_added(self) -> None:
        self.tracker.add_tag.side_effect = None

        result = self._add_many(['beta', 'ghost'])

        self.assertEqual([r['repository_id'] for r in result['repositories']], ['beta'])
        [rejected] = result['rejected']
        self.assertEqual(rejected['repository_id'], 'ghost')
        self.assertIn('not in the kato inventory', rejected['error'])
        self.assertNotIn('kato:repo:ghost', self._tags_on_disk())

    def test_when_none_can_be_added_the_failure_names_why(self) -> None:
        result = self._add_many(['ghost', 'phantom'])

        self.assertFalse(result['added'])
        self.assertIn('not in the kato inventory', result['error'])
        self.assertEqual([r['repository_id'] for r in result['rejected']], ['ghost', 'phantom'])
        self.sync.assert_not_called()

    def test_duplicates_blanks_and_a_bare_string_are_tidied(self) -> None:
        result = self._add_many(['funnel-core-lib', ' FUNNEL-CORE-LIB ', '', None])
        self.assertEqual([r['repository_id'] for r in result['repositories']], ['funnel-core-lib'])
        single = self._add_many('funnel-core-lib')
        self.assertEqual([r['repository_id'] for r in single['repositories']], ['funnel-core-lib'])

    def test_every_repository_that_is_missing_from_the_task_after_the_sync_is_reported(self) -> None:

        result = self._add_many(['beta', 'gamma'], {
            'synced': True, 'added_repositories': ['beta'], 'already_present': ['alpha'],
        })

        self.assertFalse(result['sync']['synced'])
        self.assertEqual([f['repository_id'] for f in result['sync']['failed_repositories']], ['gamma'])

    def test_a_tag_that_cannot_be_recorded_rejects_only_that_repository(self) -> None:
        real = self.workspaces.add_tag

        def flaky(task_id, tag):
            if tag.endswith('beta'):
                raise LookupError('no workspace for task')
            return real(task_id, tag)

        with patch.object(self.workspaces, 'add_tag', side_effect=flaky):
            result = self._add_many(['beta', 'gamma'])

        self.assertEqual([r['repository_id'] for r in result['repositories']], ['gamma'])
        [rejected] = result['rejected']
        self.assertEqual(rejected['repository_id'], 'beta')
        self.assertIn('failed to tag task', rejected['error'])

    def test_each_repository_is_tagged_on_the_ticket_and_one_refusal_does_not_hide_the_others(self) -> None:
        # The tracker accepts gamma's tag and refuses beta's; each outcome is
        # reported against ITS repository, in the order they were picked, and the
        # refusal neither blocks gamma's ticket tag nor the add itself.
        def tracker(issue_id, tag_name):
            if tag_name.endswith('beta'):
                raise RuntimeError('400 Client Error: Bad Request')

        self.tracker.add_tag.side_effect = tracker

        result = self._add_many(['beta', 'gamma'])

        self.assertEqual(
            sorted(call.args for call in self.tracker.add_tag.call_args_list),
            [(self.task_id, 'kato:repo:beta'), (self.task_id, 'kato:repo:gamma')],
        )
        by_repo = {r['repository_id']: r['ticket_tag'] for r in result['repositories']}
        self.assertEqual(by_repo['beta']['status'], 'failed')
        self.assertIn('400 Client Error', by_repo['beta']['error'])
        self.assertEqual(by_repo['gamma'], {'status': 'added', 'error': ''})
        self.assertEqual([r['repository_id'] for r in result['repositories']], ['beta', 'gamma'])
        self.assertTrue(result['added'])
        # The tags are in kato whatever the tracker said.
        self.assertEqual(self._tags_on_disk(), ['kato:repo:beta', 'kato:repo:gamma'])

    def test_a_tracker_with_no_tags_is_not_asked_for_any_of_them(self) -> None:
        self.tracker.supports_tags.return_value = False

        result = self._add_many(['beta', 'gamma'])

        self.tracker.add_tag.assert_not_called()
        self.assertEqual({r['ticket_tag']['status'] for r in result['repositories']}, {'unsupported'})

    def test_the_single_form_still_answers_in_its_old_shape(self) -> None:
        result = self._add()
        self.assertEqual(
            sorted(result),
            ['added', 'repository_id', 'sync', 'tag_added', 'tag_name', 'task_id', 'ticket_tag'],
        )

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
