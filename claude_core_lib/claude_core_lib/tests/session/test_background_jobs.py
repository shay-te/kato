"""``BackgroundJobs``: a job is outstanding from its start until it is reported finished."""

from __future__ import annotations

import unittest

from claude_core_lib.claude_core_lib.session.background_jobs import (
    BackgroundJobs,
    outstanding_background_jobs,
)


def _call(tool_id: str, name: str = 'Bash', **tool_input) -> dict:
    block = {'type': 'tool_use', 'name': name, 'input': tool_input}
    if tool_id:
        block['id'] = tool_id
    return {'type': 'assistant', 'message': {'content': [block]}}


def _system(subtype: str, **fields) -> dict:
    return {'type': 'system', 'subtype': subtype, **fields}


def _user(content) -> dict:
    return {'type': 'user', 'message': {'content': content}}


class StartTests(unittest.TestCase):
    def test_run_in_background_monitor_and_workflow_start_a_job(self) -> None:
        events = [
            _call('a', run_in_background=True), _call('b', 'Monitor'), _call('c', 'Workflow'),
            _call('d', 'Agent', run_in_background=True),
        ]
        self.assertEqual(outstanding_background_jobs(events), {'a', 'b', 'c', 'd'})

    def test_a_foreground_call_starts_nothing(self) -> None:
        events = [_call('a', command='ls'), _call('b', run_in_background=False),
                  {'type': 'assistant', 'message': {'content': 'text only'}},
                  {'type': 'assistant', 'message': 'not a dict'},
                  {'type': 'assistant', 'message': {'content': ['not a block', {'type': 'text'}]}}]
        self.assertEqual(outstanding_background_jobs(events), frozenset())

    def test_a_call_without_an_id_is_still_followed(self) -> None:
        jobs = BackgroundJobs()
        jobs.observe(_call('', 'Monitor'))
        jobs.observe(_call('', 'Workflow'))
        self.assertEqual(jobs.outstanding, {'unnamed-tool-use-1', 'unnamed-tool-use-2'})

    def test_task_started_backgrounded_starts_one_and_foreground_does_not(self) -> None:
        events = [
            _system('task_started', task_id='t1', tool_use_id='u1', is_backgrounded=True),
            _system('task_started', task_id='t2', tool_use_id='u2', is_backgrounded=False),
            _system('task_started', task_id='t3', is_backgrounded=True),
        ]
        self.assertEqual(outstanding_background_jobs(events), {'u1', 't3'})

    def test_task_started_links_a_known_call_to_its_task(self) -> None:
        jobs = BackgroundJobs()
        jobs.observe(_call('u1', run_in_background=True))
        jobs.observe(_system('task_started', task_id='t1', tool_use_id='u1', is_backgrounded=False))
        jobs.observe(_system('task_updated', task_id='t1', patch={'status': 'completed'}))
        self.assertEqual(jobs.outstanding, frozenset())

    def test_what_is_not_an_event_is_ignored(self) -> None:
        jobs = BackgroundJobs()
        for raw in (None, 'text', 3, ['list'], {'type': 'result'}, _system('init')):
            jobs.observe(raw)
        self.assertEqual(jobs.outstanding, frozenset())


class FinishTests(unittest.TestCase):
    def test_finished_inside_its_own_turn_is_not_outstanding(self) -> None:
        # UNA-3416, as the CLI sent it.
        events = [
            _call('toolu_npm', command='npm ci', run_in_background=True),
            _system('background_tasks_changed', tasks=[{'task_id': 'b1'}]),
            _system('task_started', task_id='b1', tool_use_id='toolu_npm', is_backgrounded=True),
            {'type': 'assistant'},
            _system('task_updated', task_id='b1', patch={'status': 'completed'}),
            _system('task_notification', task_id='b1', tool_use_id='toolu_npm', status='completed'),
            _system('background_tasks_changed', tasks=[]),
            {'type': 'result'},
        ]
        self.assertEqual(outstanding_background_jobs(events), frozenset())

    def test_a_notification_ends_its_own_job_only(self) -> None:
        events = [
            _call('a', run_in_background=True), _call('b', 'Monitor'),
            _system('task_notification', tool_use_id='a', status='completed'),
        ]
        self.assertEqual(outstanding_background_jobs(events), {'b'})

    def test_a_notification_by_task_id_ends_its_job(self) -> None:
        events = [
            _system('task_started', task_id='t1', tool_use_id='u1', is_backgrounded=True),
            _system('task_notification', task_id='t1', status='killed'),
        ]
        self.assertEqual(outstanding_background_jobs(events), frozenset())

    def test_task_updated_ends_a_job_only_with_a_finished_status(self) -> None:
        started = _system('task_started', task_id='t1', tool_use_id='u1', is_backgrounded=True)
        for status in ('completed', 'failed', 'killed', 'stopped', 'cancelled', 'error'):
            with self.subTest(status=status):
                events = [started, _system('task_updated', task_id='t1', patch={'status': status})]
                self.assertEqual(outstanding_background_jobs(events), frozenset())
        for patch in ({'status': 'running'}, {}, 'not a dict'):
            with self.subTest(patch=patch):
                events = [started, _system('task_updated', task_id='t1', patch=patch)]
                self.assertEqual(outstanding_background_jobs(events), {'u1'})

    def test_an_older_cli_s_notification_text_names_its_job(self) -> None:
        events = [
            _call('a', 'Monitor'), _call('b', 'Workflow'),
            _user('<task-notification>\n<task-id>x</task-id>\n<tool-use-id>a</tool-use-id>\n'),
        ]
        self.assertEqual(outstanding_background_jobs(events), {'b'})

    def test_notification_text_in_content_blocks_is_read_too(self) -> None:
        events = [
            _call('a', 'Monitor'),
            _user([{'type': 'text', 'text': '  <task-notification><tool-use-id>a</tool-use-id>'},
                   {'type': 'image'}, 'not a block']),
        ]
        self.assertEqual(outstanding_background_jobs(events), frozenset())

    def test_a_notification_that_names_nothing_ends_every_job(self) -> None:
        events = [_call('a', 'Monitor'), _call('b', 'Workflow'),
                  _user('<task-notification>\n<status>completed</status>\n</task-notification>')]
        self.assertEqual(outstanding_background_jobs(events), frozenset())

    def test_a_person_s_message_ends_nothing(self) -> None:
        events = [_call('a', 'Monitor'), _user('any update?'), _user(None),
                  {'type': 'user', 'message': 'not a dict'}]
        self.assertEqual(outstanding_background_jobs(events), {'a'})

    def test_a_newer_turn_ends_nothing(self) -> None:
        events = [_call('a', 'Monitor'), {'type': 'result'}, {'type': 'assistant'}, {'type': 'result'}]
        self.assertEqual(outstanding_background_jobs(events), {'a'})


class SnapshotTests(unittest.TestCase):
    def test_the_cli_s_list_ends_a_job_whose_task_it_no_longer_lists(self) -> None:
        events = [
            _system('task_started', task_id='t1', tool_use_id='u1', is_backgrounded=True),
            _system('task_started', task_id='t2', tool_use_id='u2', is_backgrounded=True),
            _system('background_tasks_changed', tasks=[{'task_id': 't2'}]),
        ]
        self.assertEqual(outstanding_background_jobs(events), {'u2'})

    def test_the_cli_s_list_adds_a_running_task_not_followed_yet(self) -> None:
        events = [_system('background_tasks_changed', tasks=[{'task_id': 't9'}, {'task_id': ''}, 'x'])]
        self.assertEqual(outstanding_background_jobs(events), {'t9'})

    def test_a_job_whose_task_is_unknown_waits_for_its_own_report(self) -> None:
        # A Workflow call the CLI never named a task for is not ended by a list.
        events = [_call('w', 'Workflow'), _system('background_tasks_changed', tasks=[])]
        self.assertEqual(outstanding_background_jobs(events), {'w'})

    def test_a_list_that_is_not_a_list_means_nothing_runs(self) -> None:
        events = [
            _system('task_started', task_id='t1', tool_use_id='u1', is_backgrounded=True),
            _system('background_tasks_changed', tasks=None),
        ]
        self.assertEqual(outstanding_background_jobs(events), frozenset())


if __name__ == '__main__':
    unittest.main()
