import shutil
import tempfile
import unittest
from pathlib import Path

from kato_core_lib.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    StatusFields,
    TaskFields,
)
from kato_core_lib.data_layers.service.agent_state_registry import AgentStateRegistry
from kato_core_lib.helpers.processed_review_comments_store import (
    read_processed_map,
    write_processed_map,
)


class AgentStateRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = AgentStateRegistry()

    def test_mark_task_processed_round_trips_pull_requests(self) -> None:
        pull_requests = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
            }
        ]

        self.registry.mark_task_processed('PROJ-1', pull_requests)

        self.assertIn('PROJ-1', self.registry.processed_task_map)
        self.assertEqual(
            self.registry.processed_task_map['PROJ-1'][PullRequestFields.PULL_REQUESTS],
            pull_requests,
        )
        self.assertEqual(
            self.registry.processed_task_map['PROJ-1'][StatusFields.STATUS],
            StatusFields.READY_FOR_REVIEW,
        )

    def test_remember_pull_request_context_and_pull_request_context_round_trip(self) -> None:
        pull_request = {
            PullRequestFields.REPOSITORY_ID: 'client',
            PullRequestFields.ID: '17',
            PullRequestFields.TITLE: 'PROJ-1 fix it already',
        }

        self.registry.remember_pull_request_context(
            pull_request,
            'feature/proj-1/client',
            agent_session_id='conversation-1',
            task_id='PROJ-1',
            task_summary='fix it already',
        )

        self.assertEqual(
            self.registry.pull_request_context('17', 'client'),
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.TITLE: 'PROJ-1 fix it already',
                'branch_name': 'feature/proj-1/client',
                ImplementationFields.AGENT_SESSION_ID: 'conversation-1',
                'task_id': 'PROJ-1',
                'task_summary': 'fix it already',
            },
        )
        self.assertEqual(self.registry.task_id_for_pull_request('17', 'client'), 'PROJ-1')

    def test_pull_request_context_raises_on_ambiguous_pr_id(self) -> None:
        self.registry.pull_request_context_map['17'] = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1/client',
            },
            {
                PullRequestFields.REPOSITORY_ID: 'backend',
                'branch_name': 'feature/proj-1/backend',
            },
        ]

        with self.assertRaisesRegex(ValueError, 'ambiguous pull request id across repositories'):
            self.registry.pull_request_context('17')

    def test_pull_request_context_disambiguates_when_repository_id_is_provided(self) -> None:
        self.registry.pull_request_context_map['17'] = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                'branch_name': 'feature/proj-1/client',
            },
            {
                PullRequestFields.REPOSITORY_ID: 'backend',
                'branch_name': 'feature/proj-1/backend',
            },
        ]

        self.assertEqual(
            self.registry.pull_request_context('17', 'backend'),
            {
                PullRequestFields.REPOSITORY_ID: 'backend',
                'branch_name': 'feature/proj-1/backend',
            },
        )

    def test_task_id_for_pull_request_falls_back_to_processed_task_map_and_caches_result(self) -> None:
        self.registry.mark_task_processed(
            'PROJ-1',
            [
                {
                    PullRequestFields.REPOSITORY_ID: 'client',
                    PullRequestFields.ID: '17',
                }
            ],
        )
        self.registry.pull_request_task_map.clear()

        self.assertEqual(self.registry.task_id_for_pull_request('17', 'client'), 'PROJ-1')
        self.assertEqual(
            self.registry.pull_request_task_map[('client', '17')],
            'PROJ-1',
        )

    def test_task_id_for_pull_request_returns_empty_string_when_unknown(self) -> None:
        self.assertEqual(self.registry.task_id_for_pull_request('17', 'client'), '')

    def test_session_ids_for_task_normalizes_stored_session_ids(self) -> None:
        self.registry.pull_request_context_map['17'] = [
            {
                TaskFields.ID: 'PROJ-1',
                ImplementationFields.AGENT_SESSION_ID: '  conversation-1\n',
            },
            {
                TaskFields.ID: 'PROJ-1',
                ImplementationFields.AGENT_SESSION_ID: 'conversation-1',
            },
        ]

        self.assertEqual(
            self.registry.session_ids_for_task('PROJ-1'),
            ['conversation-1'],
        )

    def test_review_comment_processed_round_trip(self) -> None:
        self.assertFalse(self.registry.is_review_comment_processed('client', '17', '99'))

        self.registry.mark_review_comment_processed('client', '17', '99')

        self.assertTrue(self.registry.is_review_comment_processed('client', '17', '99'))

    def test_remember_pull_request_context_deduplicates_same_repository_and_branch(self) -> None:
        pull_request = {
            PullRequestFields.REPOSITORY_ID: 'client',
            PullRequestFields.ID: '17',
        }

        self.registry.remember_pull_request_context(pull_request, 'PROJ-1')
        self.registry.remember_pull_request_context(pull_request, 'PROJ-1')

        self.assertEqual(
            self.registry.pull_request_context_map['17'],
            [
                {
                    PullRequestFields.REPOSITORY_ID: 'client',
                    'branch_name': 'PROJ-1',
                }
            ],
        )

    def test_tracked_task_ids_skips_blank_task_id_in_pull_request_task_map(self) -> None:
        # Branch 135->134: ``if task_id:`` falsy branch in the
        # pull_request_task_map loop → entry skipped, loop continues.
        self.registry.pull_request_task_map[('client', '17')] = ''
        self.registry.pull_request_task_map[('client', '18')] = 'PROJ-2'

        self.assertEqual(self.registry.tracked_task_ids(), {'PROJ-2'})

    def test_tracked_task_ids_skips_blank_task_id_in_pr_context(self) -> None:
        # Branch 140->138: ``if task_id:`` falsy branch in the
        # pull_request_context_map loop → context skipped, inner loop
        # continues to the next context.
        self.registry.pull_request_context_map['17'] = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                TaskFields.ID: '   ',  # blank after .strip()
            },
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                TaskFields.ID: 'PROJ-3',
            },
        ]

        self.assertEqual(self.registry.tracked_task_ids(), {'PROJ-3'})

    def test_task_id_for_pull_request_skips_non_list_pull_requests_in_processed_map(self) -> None:
        # Branch 200->196: ``if not isinstance(pull_requests, list): continue``
        # → loop moves to the next processed task.
        self.registry.processed_task_map['PROJ-bad'] = {
            StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
            PullRequestFields.PULL_REQUESTS: 'corrupt-not-a-list',
        }
        self.registry.processed_task_map['PROJ-good'] = {
            StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
            PullRequestFields.PULL_REQUESTS: [
                {
                    PullRequestFields.ID: '17',
                    PullRequestFields.REPOSITORY_ID: 'client',
                }
            ],
        }

        self.assertEqual(
            self.registry.task_id_for_pull_request('17', 'client'),
            'PROJ-good',
        )

    def test_task_id_for_pull_request_keeps_scanning_when_entry_does_not_match(self) -> None:
        # Branch 209->200: inner ``if`` is False → loop continues to the
        # next pull-request entry in the same processed task before
        # eventually returning ''.
        self.registry.processed_task_map['PROJ-1'] = {
            StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
            PullRequestFields.PULL_REQUESTS: [
                {
                    PullRequestFields.ID: '99',
                    PullRequestFields.REPOSITORY_ID: 'backend',
                },
                {
                    PullRequestFields.ID: '100',
                    PullRequestFields.REPOSITORY_ID: 'client',
                },
            ],
        }

        # Lookup for ('17','client') matches neither entry → falls through
        # the inner loop without setting pull_request_task_map.
        self.assertEqual(
            self.registry.task_id_for_pull_request('17', 'client'), '',
        )
        self.assertNotIn(('client', '17'), self.registry.pull_request_task_map)

    def test_task_id_for_pull_request_skips_non_dict_pull_request_entry(self) -> None:
        # Inner ``if not isinstance(pull_request, dict): continue`` path —
        # included alongside the 209->200 case so both inner-loop
        # branches are exercised together.
        self.registry.processed_task_map['PROJ-1'] = {
            StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
            PullRequestFields.PULL_REQUESTS: [
                'not-a-dict',
                {
                    PullRequestFields.ID: '17',
                    PullRequestFields.REPOSITORY_ID: 'client',
                },
            ],
        }

        self.assertEqual(
            self.registry.task_id_for_pull_request('17', 'client'),
            'PROJ-1',
        )


class AgentStateRegistryPersistenceTests(unittest.TestCase):
    """Processed-review-comment marks survive a restart and are dropped on
    forget — the fix for a still-open comment being re-worked every restart,
    without leaving deleted-task state behind in ~/.kato.
    """

    def setUp(self) -> None:
        self._dir = tempfile.mkdtemp()
        self.path = Path(self._dir) / 'processed_review_comments.json'

    def tearDown(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_mark_persists_and_survives_restart(self) -> None:
        registry = AgentStateRegistry(processed_review_comments_path=self.path)
        registry.mark_review_comment_processed('client', '17', 'c-1')
        self.assertTrue(self.path.is_file())

        # A brand-new registry on the same path IS a kato restart.
        restarted = AgentStateRegistry(processed_review_comments_path=self.path)
        self.assertTrue(restarted.is_review_comment_processed('client', '17', 'c-1'))
        # An unrelated comment is still new — only handled ones are suppressed.
        self.assertFalse(restarted.is_review_comment_processed('client', '17', 'c-2'))

    def test_default_no_path_is_in_memory_only(self) -> None:
        registry = AgentStateRegistry()
        registry.mark_review_comment_processed('client', '17', 'c-1')
        self.assertTrue(registry.is_review_comment_processed('client', '17', 'c-1'))
        self.assertFalse(self.path.exists())  # nothing written to disk

    def test_forget_task_clears_persisted_marks(self) -> None:
        registry = AgentStateRegistry(processed_review_comments_path=self.path)
        registry.remember_pull_request_context(
            {PullRequestFields.ID: '17', PullRequestFields.REPOSITORY_ID: 'client'},
            'feature/proj-1',
            task_id='PROJ-1',
        )
        registry.mark_review_comment_processed('client', '17', 'c-1')

        registry.forget_task('PROJ-1')

        self.assertFalse(registry.is_review_comment_processed('client', '17', 'c-1'))
        # The on-disk file no longer holds the deleted task's marks.
        self.assertEqual(read_processed_map(self.path), {})
        # And a restart does not resurrect them.
        restarted = AgentStateRegistry(processed_review_comments_path=self.path)
        self.assertFalse(restarted.is_review_comment_processed('client', '17', 'c-1'))

    def test_forget_task_keeps_other_tasks_marks(self) -> None:
        registry = AgentStateRegistry(processed_review_comments_path=self.path)
        for task_id, pr_id in (('PROJ-1', '17'), ('PROJ-2', '18')):
            registry.remember_pull_request_context(
                {PullRequestFields.ID: pr_id, PullRequestFields.REPOSITORY_ID: 'client'},
                'feature/' + task_id.lower(),
                task_id=task_id,
            )
            registry.mark_review_comment_processed('client', pr_id, 'c-' + pr_id)

        registry.forget_task('PROJ-1')

        self.assertFalse(registry.is_review_comment_processed('client', '17', 'c-17'))
        self.assertTrue(registry.is_review_comment_processed('client', '18', 'c-18'))

    def test_read_processed_map_tolerates_missing_and_malformed(self) -> None:
        self.assertEqual(read_processed_map(self.path), {})   # missing file
        self.assertEqual(read_processed_map(None), {})        # no path
        self.path.write_text('not json{{', encoding='utf-8')
        self.assertEqual(read_processed_map(self.path), {})   # corrupt json
        self.path.write_text('{"not": "a list"}', encoding='utf-8')
        self.assertEqual(read_processed_map(self.path), {})   # wrong shape

    def test_store_round_trip(self) -> None:
        original = {('client', '17'): {'c-1', 'c-2'}, ('server', '3'): {'c-9'}}
        write_processed_map(self.path, original)
        self.assertEqual(read_processed_map(self.path), original)
