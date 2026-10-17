"""Kato's tracker adapters for the plan queue: a plan's steps as sub-tasks.

A fake tracker client speaks the same sub-task contract the YouTrack and Jira
clients implement (``IssueClientBase.supports_subtasks``); every adapter is
exercised against it, plus real files for this kato's id and real ``Task``-like
records for the pickup gate and repository resolution.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan, PlanMode, PlanStatus, Step
from plan_queue_core_lib.plan_queue_core_lib.errors import PlanQueueError
from plan_queue_core_lib.plan_queue_core_lib.plan_record import render_record
from plan_queue_core_lib.plan_queue_core_lib.ports import LaunchRequest, ProbeError, TaskPhase
from provider_client_base.provider_client_base.data.subtask import IssueComment, IssueStatus

from kato_core_lib.data_layers.service.plan_queue.history_probe import GitHistoryProbe
from kato_core_lib.data_layers.service.plan_queue.ownership import KatoOwnership
from kato_core_lib.data_layers.service.plan_queue.plan_record_on_tracker import (
    TrackerRecordPublisher,
    TrackerRecordSource,
    record_intro,
)
from kato_core_lib.data_layers.service.plan_queue.step_footer import (
    parse_footer,
    subtask_description,
    subtask_summary,
)
from kato_core_lib.data_layers.service.plan_queue.step_gate import plan_step_wait_reason
from kato_core_lib.data_layers.service.plan_queue.tracker_launcher import (
    COUNTED_DONE_COMMENT,
    RELEASED_AGAIN_COMMENT,
    TrackerStepLauncher,
)
from kato_core_lib.data_layers.service.plan_queue.tracker_progress import (
    RoutedTaskProgress,
    TrackerTaskProgress,
)
from kato_core_lib.data_layers.service.plan_queue.wiring import plan_mode
from kato_core_lib.helpers.agent_comment_classification import (
    PLAN_RECORD_COMMENT_PREFIX,
    active_execution_blocking_comment,
    is_agent_operational_comment,
)

PLAN_ID = 'c' * 32
STATES = {'field': 'State', 'progress': 'In Progress', 'review': 'To Verify', 'done': 'Done'}


class FakeSubtaskTracker(object):
    """A tracker client with sub-tasks: issues, their comments, who wrote them."""

    supports_subtasks = True

    def __init__(self, me: str = 'shay') -> None:
        self.me = me
        self.issues: dict[str, dict] = {}
        self.comments: dict[str, list[IssueComment]] = {}
        self.assigned: list[tuple[str, str]] = []
        self.status_reads: list[list[str]] = []
        self._next = 10

    def issue(self, issue_id, *, state='Open', assignee='', name='', parent='', description='', created='') -> None:
        self.issues[issue_id] = dict(state=state, assignee=assignee, name=name, parent=parent,
                                     description=description, created=created or '2026-10-17T10:00:00+00:00')

    def comment(self, issue_id, body, *, author='shay', comment_id='') -> str:
        comment_id = comment_id or f'c{len(self.comments.get(issue_id, [])) + 1}'
        self.comments.setdefault(issue_id, []).insert(0, IssueComment(comment_id, author, author.title(), body))
        return comment_id

    # ----- the contract -----

    def create_subtask(self, parent_id, summary, description):
        self._next += 1
        issue_id = f'PROJ-{self._next}'
        self.issue(issue_id, parent=parent_id, description=description)
        self.issues[issue_id]['summary'] = summary
        return issue_id

    def find_subtask(self, parent_id, marker):
        return next((issue_id for issue_id, issue in self.issues.items()
                     if issue['parent'] == parent_id and marker in issue['description']), '')

    def assign_issue(self, issue_id, assignee):
        self.assigned.append((issue_id, assignee))
        self.issues[issue_id]['assignee'] = assignee

    def get_issues_status(self, issue_ids, state_field):
        self.status_reads.append(list(issue_ids))
        found = {}
        for issue_id in issue_ids:
            issue = self.issues.get(issue_id)
            found[issue_id] = (IssueStatus(issue_id, exists=False) if issue is None else IssueStatus(
                issue_id, state=issue['state'], assignee=issue['assignee'], assignee_name=issue['name'],
                created_at=issue['created'], parent_id=issue['parent']))
        return found

    def list_comments(self, issue_id):
        return list(self.comments.get(issue_id, []))

    def add_preformatted_comment(self, issue_id, intro, preformatted):
        return self.comment(issue_id, f'{intro}\n\n```\n{preformatted}\n```', author=self.me)

    def update_preformatted_comment(self, issue_id, comment_id, intro, preformatted):
        comments = self.comments[issue_id]
        index = next(i for i, comment in enumerate(comments) if comment.comment_id == comment_id)
        comments[index] = IssueComment(comment_id, self.me, self.me.title(), f'{intro}\n\n```\n{preformatted}\n```')

    def current_user(self):
        return self.me


class Services(object):
    """Kato's task services around one tracker client."""

    def __init__(self, tracker, *, bot_login='me') -> None:
        self.tracker = tracker
        self.comments: list[tuple[str, str]] = []
        self.moved_done: list[str] = []
        self.task_service = SimpleNamespace(
            subtask_tracker=lambda: self.tracker, bot_login=bot_login,
            add_comment=lambda issue_id, text: self.comments.append((issue_id, text)),
        )
        self.task_state_service = SimpleNamespace(
            state_names=lambda: dict(STATES), move_task_to_done=self.moved_done.append,
        )


def request(step_id='ui', *, waits_for=('PROJ-11',), repositories=('web',)) -> LaunchRequest:
    return LaunchRequest(
        plan_id=PLAN_ID, step_id=step_id, index=2, count=3, title='Use the endpoint',
        description='Step 2 of 3 of the plan.\n\nDo it.', repositories=repositories,
        source_task_id='PROJ-1', plan_title='Discounts', waits_for=waits_for,
    )


class FooterTests(unittest.TestCase):
    def test_a_sub_task_says_where_it_belongs_and_reads_back(self) -> None:
        text = subtask_description(request(), ['demo-shop-fe'])
        self.assertTrue(text.startswith('Step 2 of 3 of the plan.\n\nDo it.\n\nKato plan queue\n'))
        self.assertIn('Part of PROJ-1 · task 2 of 3', text)
        self.assertIn('Waits for: PROJ-11 (merged and done before this starts)', text)
        self.assertTrue(text.endswith(f'plan-step:{PLAN_ID}/ui'))
        footer = parse_footer(f'Some comment\n{text}\nMore text')
        self.assertEqual((footer.source_task_id, footer.plan_id, footer.step_id, footer.repositories),
                         ('PROJ-1', PLAN_ID, 'ui', ('demo-shop-fe',)))
        self.assertEqual(subtask_summary(request()), 'Subtask of PROJ-1: Use the endpoint')

    def test_every_repository_and_no_wait(self) -> None:
        text = subtask_description(request(waits_for=(), repositories=()), [])
        self.assertIn('Waits for: nothing', text)
        self.assertEqual(parse_footer(text).repositories, ())

    def test_no_footer(self) -> None:
        for text in ('', None, 'Part of PROJ-1 · task 1 of 2', f'plan-step:{PLAN_ID}/ui'):
            self.assertIsNone(parse_footer(text))


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = FakeSubtaskTracker()
        self.services = Services(self.tracker)
        repositories = SimpleNamespace(get_repository=lambda repo_id: (
            SimpleNamespace(repo_slug='demo-shop-fe') if repo_id == 'web' else (_ for _ in ()).throw(KeyError(repo_id))))
        self.launcher = TrackerStepLauncher(
            task_service=self.services.task_service, task_state_service=self.services.task_state_service,
            repository_service=repositories, ownership=SimpleNamespace(user=lambda: 'shay'),
        )

    def test_prepare_creates_an_unassigned_sub_task_once(self) -> None:
        self.assertTrue(self.launcher.available())
        task_id = self.launcher.prepare(request(repositories=('web', 'gone')))
        issue = self.tracker.issues[task_id]
        self.assertEqual((issue['parent'], issue['assignee'], issue['summary']),
                         ('PROJ-1', '', 'Subtask of PROJ-1: Use the endpoint'))
        self.assertIn('Repositories: demo-shop-fe, gone', issue['description'])
        self.assertEqual(self.launcher.prepare(request()), task_id)  # a retry finds the first
        self.assertEqual(len(self.tracker.issues), 1)

    def test_release_assigns_to_the_user_kato_polls_for_and_never_over_someone(self) -> None:
        self.tracker.issue('PROJ-5')
        self.assertEqual(self.launcher.release(request(), 'PROJ-5'), 'PROJ-5')
        self.assertEqual(self.tracker.assigned, [('PROJ-5', 'shay')])
        self.tracker.issue('PROJ-6', assignee='dana')
        self.launcher.release(request(), 'PROJ-6')
        self.assertEqual(self.tracker.issues['PROJ-6']['assignee'], 'dana')
        with self.assertRaises(RuntimeError):
            self.launcher.release(request(), 'PROJ-404')

    def test_a_configured_assignee_wins_over_the_token_user(self) -> None:
        self.services.task_service.bot_login = 'kato-bot'
        self.assertEqual(self.launcher.assignee(), 'kato-bot')
        self.services.task_service.bot_login = 'currentUser()'
        self.assertEqual(self.launcher.assignee(), 'shay')

    def test_a_handed_back_task_gets_the_pickup_unblocked(self) -> None:
        self.tracker.issue('PROJ-7')
        self.tracker.comment('PROJ-7', 'Kato agent started working on this task')
        self.launcher.release(request(), 'PROJ-7')
        self.assertEqual(self.services.comments, [('PROJ-7', RELEASED_AGAIN_COMMENT)])
        self.assertEqual(active_execution_blocking_comment([
            {'body': 'Kato agent started working on this task'}, {'body': RELEASED_AGAIN_COMMENT},
        ]), '')

    def test_withdraw_leaves_a_note_only_on_an_untaken_task(self) -> None:
        self.tracker.issue('PROJ-8')
        self.tracker.issue('PROJ-9', assignee='dana')
        for task_id in ('PROJ-8', 'PROJ-9', 'PROJ-404'):
            self.launcher.withdraw(task_id, 'the plan was stopped')
        self.assertEqual(len(self.services.comments), 1)
        self.assertTrue(self.services.comments[0][1].startswith('Kato plan queue: the plan was stopped.'))

    def test_counted_done_moves_the_ticket_and_says_so(self) -> None:
        self.launcher.counted_done('PROJ-8')
        self.assertEqual(self.services.moved_done, ['PROJ-8'])
        self.assertEqual(self.services.comments, [('PROJ-8', COUNTED_DONE_COMMENT)])

    def test_no_sub_task_tracker(self) -> None:
        self.tracker = None
        self.services.tracker = None
        self.assertFalse(self.launcher.available())
        with self.assertRaises(RuntimeError):
            self.launcher.prepare(request())


class ProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = FakeSubtaskTracker()
        self.services = Services(self.tracker)
        self.now = [1000.0]
        self.workspaces = {}
        self.progress = TrackerTaskProgress(
            task_service=self.services.task_service, task_state_service=self.services.task_state_service,
            workspace_manager=SimpleNamespace(get=self.workspaces.get), clock=lambda: self.now[0],
        )

    def test_each_tracker_state_maps_to_a_phase(self) -> None:
        cases = {
            'PROJ-1': (dict(state='Open'), TaskPhase.UNRELEASED),
            'PROJ-2': (dict(state='Open', assignee='dana', name='Dana'), TaskPhase.WAITING),
            'PROJ-3': (dict(state='In Progress', assignee='dana'), TaskPhase.IN_PROGRESS),
            'PROJ-4': (dict(state='to verify', assignee='dana'), TaskPhase.IN_REVIEW),
            'PROJ-5': (dict(state='Done'), TaskPhase.DONE),
        }
        for issue_id, (fields, _phase) in cases.items():
            self.tracker.issue(issue_id, **fields)
        seen = self.progress.observe([*cases, 'PROJ-404'])
        for issue_id, (_fields, phase) in cases.items():
            self.assertEqual(seen[issue_id].phase, phase, issue_id)
        self.assertEqual((seen['PROJ-2'].assignee, seen['PROJ-2'].assignee_name), ('dana', 'Dana'))
        self.assertEqual(seen['PROJ-1'].created_at, 1792231200.0)
        self.assertEqual(seen['PROJ-404'].phase, TaskPhase.MISSING)

    def test_what_this_kato_knows_about_its_own_run(self) -> None:
        self.tracker.issue('PROJ-1', state='In Progress', assignee='shay')
        self.tracker.issue('PROJ-2', state='Open', assignee='shay', created='not a date')
        self.workspaces['PROJ-1'] = SimpleNamespace(status='errored')
        self.workspaces['PROJ-2'] = SimpleNamespace(status='provisioning')
        seen = self.progress.observe(['PROJ-1', 'PROJ-2'])
        self.assertEqual((seen['PROJ-1'].phase, seen['PROJ-1'].detail),
                         (TaskPhase.ERRORED, 'its run on this kato hit an error'))
        self.assertEqual((seen['PROJ-2'].phase, seen['PROJ-2'].created_at), (TaskPhase.PREPARING, 0.0))

    def test_the_tracker_is_read_once_a_minute(self) -> None:
        self.tracker.issue('PROJ-1')
        self.progress.observe(['PROJ-1'])
        self.progress.observe(['PROJ-1'])
        self.assertEqual(len(self.tracker.status_reads), 1)
        self.now[0] += 60
        self.progress.observe(['PROJ-1'])
        self.assertEqual(len(self.tracker.status_reads), 2)

    def test_no_tracker_or_no_tasks_is_unknown(self) -> None:
        self.assertEqual(self.progress.observe([]), {})
        self.services.tracker = None
        self.assertEqual(self.progress.observe(['PROJ-1']), {})

    def test_a_workspace_that_cannot_be_read_counts_as_none(self) -> None:
        self.tracker.issue('PROJ-1', state='In Progress')
        broken = TrackerTaskProgress(
            task_service=self.services.task_service, task_state_service=self.services.task_state_service,
            workspace_manager=SimpleNamespace(get=mock.Mock(side_effect=OSError('gone'))),
        )
        self.assertEqual(broken.observe(['PROJ-1'])['PROJ-1'].phase, TaskPhase.IN_PROGRESS)
        no_workspaces = TrackerTaskProgress(
            task_service=self.services.task_service, task_state_service=self.services.task_state_service,
            workspace_manager=None,
        )
        self.assertEqual(no_workspaces.observe(['PROJ-1'])['PROJ-1'].phase, TaskPhase.IN_PROGRESS)

    def test_local_tasks_go_to_the_local_adapter(self) -> None:
        local = mock.Mock(observe=mock.Mock(return_value={'LOCAL-1': 'local'}))
        tracker = mock.Mock(observe=mock.Mock(return_value={'PROJ-1': 'tracker'}))
        routed = RoutedTaskProgress(local=local, tracker=tracker, is_local=lambda task_id: task_id.startswith('LOCAL'))
        self.assertEqual(routed.observe(['LOCAL-1', 'PROJ-1']), {'LOCAL-1': 'local', 'PROJ-1': 'tracker'})
        local.observe.reset_mock()
        tracker.observe.reset_mock()
        routed.observe(['PROJ-1'])
        local.observe.assert_not_called()
        routed.observe(['LOCAL-1'])
        self.assertEqual(tracker.observe.call_count, 1)


class OwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='plan-owner-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'plan_queues'
        self.tracker = FakeSubtaskTracker(me='shay-account')
        self.services = Services(self.tracker)

    def _ownership(self):
        return KatoOwnership(task_service=self.services.task_service, root=self.root)

    def test_an_id_kept_without_a_value_is_made_again(self) -> None:
        self.root.mkdir(parents=True)
        (self.root / 'host.json').write_text('{"id": ""}', encoding='utf-8')
        self.assertEqual(len(self._ownership().identity()), 32)

    def test_this_kato_has_one_id_kept_in_its_home(self) -> None:
        ownership = self._ownership()
        first = ownership.identity()
        self.assertEqual(len(first), 32)
        self.assertEqual(ownership.identity(), first)  # read once
        self.assertEqual(self._ownership().identity(), first)
        (self.root / 'host.json').write_text('not json', encoding='utf-8')
        self.assertNotEqual(self._ownership().identity(), first)
        self.assertEqual(json.loads((self.root / 'host.json').read_text())['id'], self._ownership().identity())

    def test_the_user_is_the_token_account_and_never_a_guess(self) -> None:
        ownership = self._ownership()
        self.assertEqual(ownership.user(), 'shay-account')
        self.tracker.me = 'changed'
        self.assertEqual(ownership.user(), 'shay-account')  # read once
        self.tracker.me = ''
        with self.assertRaises(RuntimeError):
            self._ownership().user()
        self.services.tracker = None
        with self.assertRaises(RuntimeError):
            self._ownership().user()


def tracker_plan() -> Plan:
    plan = Plan(plan_id=PLAN_ID, source_task_id='PROJ-1', title='T', mode=PlanMode.TRACKER,
                status=PlanStatus.RUNNING, driver='host-shay', driver_user='shay')
    plan.steps = [Step(id='api', title='API', brief='b', task_id='PROJ-11', repositories=['api']),
                  Step(id='ui', title='UI', brief='b', task_id='PROJ-12', depends_on=['api'])]
    return plan


class RecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = FakeSubtaskTracker()
        self.services = Services(self.tracker)
        for issue_id in ('PROJ-11', 'PROJ-12'):
            self.tracker.issue(issue_id, parent='PROJ-1')
        self.publisher = TrackerRecordPublisher(task_service=self.services.task_service,
                                                ownership=SimpleNamespace(user=lambda: 'shay'))
        self.now = [0.0]
        self.source = TrackerRecordSource(task_service=self.services.task_service,
                                          task_state_service=self.services.task_state_service,
                                          clock=lambda: self.now[0])

    def test_the_driver_keeps_one_comment_and_rewrites_it(self) -> None:
        plan = tracker_plan()
        self.publisher.publish(plan)
        plan.status = PlanStatus.PAUSED
        self.publisher.publish(plan)
        comments = self.tracker.list_comments('PROJ-1')
        self.assertEqual(len(comments), 1)
        self.assertTrue(comments[0].body.startswith(record_intro(plan)))
        self.assertIn('status paused', comments[0].body)
        restarted = TrackerRecordPublisher(task_service=self.services.task_service,
                                           ownership=SimpleNamespace(user=lambda: 'shay'))
        self.tracker.comment('PROJ-1', 'a person writes something')
        plan.status = PlanStatus.RUNNING
        restarted.publish(plan)  # finds its own record again
        self.assertEqual(len(self.tracker.list_comments('PROJ-1')), 2)
        self.assertTrue(PLAN_RECORD_COMMENT_PREFIX in record_intro(plan))

    def test_a_tracker_that_gives_no_comment_id_is_found_next_time(self) -> None:
        self.tracker.add_preformatted_comment = lambda *args: (FakeSubtaskTracker.add_preformatted_comment(self.tracker, *args) and '')
        self.publisher.publish(tracker_plan())
        self.publisher.publish(tracker_plan())
        self.assertEqual(len(self.tracker.list_comments('PROJ-1')), 1)
        self.services.tracker = None
        with self.assertRaises(RuntimeError):
            self.publisher.publish(tracker_plan())

    def test_readers_take_the_newest_record_whose_tasks_are_real_sub_tasks(self) -> None:
        self.assertIsNone(self.source.latest('PROJ-1'))
        self.tracker.comment('PROJ-1', render_record(tracker_plan()), author='shay')
        self.now[0] += 60
        self.assertEqual(self.source.latest('PROJ-1').author, 'shay')
        # A record naming a ticket that is not a sub-task of PROJ-1 is skipped.
        self.tracker.issue('PROJ-99', parent='PROJ-77')
        forged = tracker_plan()
        forged.steps[1].task_id = 'PROJ-99'
        self.tracker.comment('PROJ-1', render_record(forged), author='shay')
        missing = tracker_plan()
        missing.steps[1].task_id = 'PROJ-404'
        self.tracker.comment('PROJ-1', render_record(missing), author='shay')
        other = tracker_plan()
        other.source_task_id = 'PROJ-2'
        self.tracker.comment('PROJ-1', render_record(other), author='shay')
        self.now[0] += 60
        self.assertEqual([step.task_id for step in self.source.latest('PROJ-1').steps], ['PROJ-11', 'PROJ-12'])

    def test_a_record_is_read_at_most_once_a_minute_unless_forgotten(self) -> None:
        self.assertIsNone(self.source.latest('PROJ-1'))
        self.tracker.comment('PROJ-1', render_record(tracker_plan()), author='shay')
        self.assertIsNone(self.source.latest('PROJ-1'))
        self.source.forget('proj-1')  # the same ticket, however it is spelled
        self.assertIsNotNone(self.source.latest('PROJ-1'))
        self.services.tracker = None
        self.source.forget('PROJ-1')
        self.assertIsNone(self.source.latest('PROJ-1'))


class HistoryTests(unittest.TestCase):
    def test_the_task_id_is_matched_whole_in_the_destination(self) -> None:
        service = mock.Mock()
        service.get_repository.return_value = SimpleNamespace(local_path='/repo')
        service.destination_branch.return_value = 'master'
        service.history_mentions.return_value = True
        probe = GitHistoryProbe(repository_service=service)
        self.assertTrue(probe.merged_in_history('PROJ-12', 'api', since=5.0))
        args, kwargs = service.history_mentions.call_args
        self.assertEqual(args, ('/repo', 'master', '(^|[^A-Za-z0-9_])PROJ-12([^0-9]|$)'))
        self.assertEqual(kwargs['since'], 5.0)
        self.assertFalse(probe.merged_in_history('LOCAL step; rm -rf', 'api', since=0))
        service.history_mentions.side_effect = RuntimeError('fetch failed')
        with self.assertRaises(ProbeError):
            probe.merged_in_history('PROJ-12', 'api', since=0)


class GateTests(unittest.TestCase):
    def _task(self, task_id='PROJ-12', with_footer=True):
        description = subtask_description(request(), ['web']) if with_footer else 'plain ticket'
        return SimpleNamespace(id=task_id, description=description)

    def test_a_ticket_with_no_footer_or_no_queue_is_never_held(self) -> None:
        self.assertEqual(plan_step_wait_reason(None, self._task()), '')
        self.assertEqual(plan_step_wait_reason(mock.Mock(), self._task(with_footer=False)), '')

    def test_a_sub_task_waits_until_its_parents_are_merged_and_done(self) -> None:
        queue = mock.Mock(waiting_for=mock.Mock(return_value=['PROJ-11']))
        self.assertEqual(plan_step_wait_reason(queue, self._task()), 'it waits for PROJ-11 to be merged and done')
        queue.waiting_for.return_value = []
        self.assertEqual(plan_step_wait_reason(queue, self._task()), '')

    def test_another_katos_plan_is_followed_first(self) -> None:
        queue = mock.Mock(waiting_for=mock.Mock(side_effect=[None, ['PROJ-11']]))
        self.assertEqual(plan_step_wait_reason(queue, self._task()), 'it waits for PROJ-11 to be merged and done')
        queue.follow_source.assert_called_once_with('PROJ-1')
        queue.waiting_for.side_effect = [None, None]
        self.assertIn('cannot be found there yet', plan_step_wait_reason(queue, self._task()))
        queue.waiting_for.side_effect = None
        queue.waiting_for.return_value = None
        queue.follow_source.side_effect = PlanQueueError('tracker down')
        self.assertIn('could not be read: tracker down', plan_step_wait_reason(queue, self._task()))


class PlanModeTests(unittest.TestCase):
    def test_tracker_sub_tasks_only_for_a_tracker_ticket_on_a_tracker_that_can(self) -> None:
        local_store = SimpleNamespace(owns=lambda task_id: task_id.startswith('LOCAL-'))
        tracker = SimpleNamespace(subtask_tracker=lambda: object())
        none = SimpleNamespace(subtask_tracker=lambda: None)
        plan = Plan(plan_id=PLAN_ID, source_task_id='PROJ-1')
        self.assertEqual(plan_mode(plan, task_service=tracker, local_task_store=local_store), PlanMode.TRACKER)
        self.assertEqual(plan_mode(plan, task_service=none, local_task_store=local_store), PlanMode.LOCAL)
        plan.source_task_id = 'LOCAL-3'
        self.assertEqual(plan_mode(plan, task_service=tracker, local_task_store=local_store), PlanMode.LOCAL)


class CommentClassificationTests(unittest.TestCase):
    def test_a_ticket_carrying_a_plan_is_never_implemented_whole_again(self) -> None:
        record = f'{PLAN_RECORD_COMMENT_PREFIX}PROJ-1 is split into 2 sub-tasks.'
        self.assertEqual(active_execution_blocking_comment([{'body': record}]), record)
        self.assertEqual(active_execution_blocking_comment([{'body': record}, {'body': 'kato: retry approved'}]), '')
        self.assertTrue(is_agent_operational_comment(record))
        self.assertTrue(is_agent_operational_comment(COUNTED_DONE_COMMENT))
        self.assertEqual(active_execution_blocking_comment([{'body': COUNTED_DONE_COMMENT}]), '')


if __name__ == '__main__':
    unittest.main()
