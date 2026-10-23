"""``GET /api/usage`` — tokens consumed per task, from the real transcript files.

Real transcripts in a temp ``projects`` store (the same layout Claude Code
writes: ``<projects>/<encoded cwd>/<session id>.jsonl``), real session records,
a real route. Nothing about the counting is stubbed.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask import Flask

from agent_core_lib.agent_core_lib.session.record import AgentSessionRecord
from kato_webserver.usage_routes import collect_usage, register_usage_routes
from claude_core_lib.claude_core_lib.helpers.model_pricing import PRICES_AS_OF
from claude_core_lib.claude_core_lib.helpers.transcript_usage import TranscriptUsageReader


def _turn(message_id: str, *, out: int, read: int = 0, inp: int = 0, write: int = 0,
          model: str = 'claude-opus-5', at: str = '2026-10-01T00:00:00Z') -> str:
    return json.dumps({
        'type': 'assistant', 'timestamp': at,
        'message': {'id': message_id, 'model': model, 'usage': {
            'input_tokens': inp, 'output_tokens': out,
            'cache_read_input_tokens': read, 'cache_creation_input_tokens': write,
        }},
    }) + '\n'


class UsageRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-usage-')
        self.addCleanup(self._tmp.cleanup)
        self.projects = Path(self._tmp.name) / 'projects'
        (self.projects / '-work-T1').mkdir(parents=True)
        (self.projects / '-work-T2').mkdir(parents=True)
        env = patch.dict(os.environ, {'CLAUDE_SESSIONS_ROOT': str(self.projects)})
        env.start()
        self.addCleanup(env.stop)

    def _transcript(self, project: str, session_id: str, *turns: str) -> None:
        (self.projects / project / f'{session_id}.jsonl').write_text(
            ''.join(turns), encoding='utf-8',
        )

    @staticmethod
    def _manager(*records):
        manager = MagicMock()
        manager.list_records.return_value = list(records)
        return manager

    @staticmethod
    def _workspaces(*entries):
        manager = MagicMock()
        manager.list_workspaces.return_value = [SimpleNamespace(**entry) for entry in entries]
        return manager

    def _collect(self, manager, workspaces):
        return collect_usage(manager, workspaces, TranscriptUsageReader())

    # ----- the numbers -----

    def test_reports_each_task_with_its_totals_biggest_first(self) -> None:
        self._transcript('-work-T1', 'aaaa1111', _turn('m1', out=10, read=100))
        self._transcript('-work-T2', 'bbbb2222', _turn('m2', out=500, read=9000, inp=20, write=30))
        payload = self._collect(
            self._manager(
                AgentSessionRecord(task_id='T1', task_summary='small one', agent_session_id='aaaa1111'),
                AgentSessionRecord(task_id='T2', task_summary='big one', agent_session_id='bbbb2222'),
            ),
            self._workspaces(),
        )
        self.assertEqual([t['task_id'] for t in payload['tasks']], ['T2', 'T1'])
        big = payload['tasks'][0]
        self.assertEqual(big['summary'], 'big one')
        self.assertEqual(big['usage']['output'], 500)
        self.assertEqual(big['usage']['total'], 500 + 9000 + 20 + 30)
        self.assertEqual(payload['totals']['total'], big['usage']['total'] + 110)
        self.assertEqual(payload['totals']['turns'], 2)

    def test_a_task_counts_every_chat_it_ever_had(self) -> None:
        self._transcript('-work-T1', 'old0001', _turn('a', out=100))
        self._transcript('-work-T1', 'older02', _turn('b', out=50))
        self._transcript('-work-T1', 'current', _turn('c', out=7))
        payload = self._collect(
            self._manager(AgentSessionRecord(
                task_id='T1', agent_session_id='current',
                previous_session_ids=['old0001', 'older02'],
            )),
            self._workspaces(),
        )
        task = payload['tasks'][0]
        self.assertEqual(task['chats'], 3)
        self.assertEqual(task['usage']['output'], 157)

    def test_picks_up_the_chat_only_the_workspace_remembers(self) -> None:
        self._transcript('-work-T1', 'wsonly1', _turn('a', out=42))
        payload = self._collect(
            self._manager(),
            self._workspaces({
                'task_id': 'T1', 'task_summary': 'from the workspace', 'status': 'review',
                'agent_session_id': 'wsonly1',
            }),
        )
        task = payload['tasks'][0]
        self.assertEqual(task['usage']['output'], 42)
        self.assertEqual(task['summary'], 'from the workspace')
        self.assertEqual(task['status'], 'review')

    def test_a_chat_known_to_both_records_is_counted_once(self) -> None:
        self._transcript('-work-T1', 'shared01', _turn('a', out=10))
        payload = self._collect(
            self._manager(AgentSessionRecord(task_id='T1', agent_session_id='shared01')),
            self._workspaces({
                'task_id': 'T1', 'task_summary': '', 'status': 'active',
                'agent_session_id': 'shared01',
            }),
        )
        self.assertEqual(payload['tasks'][0]['chats'], 1)
        self.assertEqual(payload['totals']['output'], 10)

    def test_ids_that_differ_only_in_case_are_one_task(self) -> None:
        self._transcript('-work-T1', 'shared01', _turn('a', out=10))
        payload = self._collect(
            self._manager(AgentSessionRecord(task_id='una-1', agent_session_id='shared01')),
            self._workspaces({
                'task_id': 'UNA-1', 'task_summary': 'x', 'status': 'active',
                'agent_session_id': 'shared01',
            }),
        )
        self.assertEqual(len(payload['tasks']), 1)
        self.assertEqual(payload['tasks'][0]['task_id'], 'UNA-1')

    def test_one_transcript_is_never_counted_for_two_tasks(self) -> None:
        self._transcript('-work-T1', 'shared01', _turn('a', out=10))
        payload = self._collect(
            self._manager(
                AgentSessionRecord(task_id='A', agent_session_id='shared01'),
                AgentSessionRecord(task_id='B', agent_session_id='shared01'),
            ),
            self._workspaces(),
        )
        self.assertEqual(len(payload['tasks']), 1)
        self.assertEqual(payload['totals']['output'], 10)

    # ----- what is left out -----

    def test_tasks_without_a_transcript_are_not_listed(self) -> None:
        payload = self._collect(
            self._manager(AgentSessionRecord(task_id='T1', agent_session_id='nowhere9')),
            self._workspaces({
                'task_id': 'T2', 'task_summary': '', 'status': 'active', 'agent_session_id': '',
            }),
        )
        self.assertEqual(payload['tasks'], [])
        self.assertEqual(payload['totals']['total'], 0)

    def test_another_agents_chats_are_named_not_silently_dropped(self) -> None:
        self._transcript('-work-T1', 'claude01', _turn('a', out=5))
        record = AgentSessionRecord(
            task_id='T1', agent_backend='codex', agent_session_id='codex-thread',
            chats_by_backend={'claude': {
                'agent_session_id': 'claude01', 'previous_session_ids': [],
            }},
        )
        payload = self._collect(self._manager(record), self._workspaces())
        task = payload['tasks'][0]
        self.assertEqual(task['usage']['output'], 5)
        self.assertEqual(task['uncounted_backends'], ['codex'])
        self.assertEqual(payload['uncounted_backends'], ['codex'])

    def test_a_record_from_before_backends_were_tracked_is_claudes(self) -> None:
        self._transcript('-work-T1', 'legacy01', _turn('a', out=3))
        record = AgentSessionRecord(task_id='T1', agent_backend='', agent_session_id='legacy01')
        payload = self._collect(self._manager(record), self._workspaces())
        self.assertEqual(payload['tasks'][0]['usage']['output'], 3)
        self.assertEqual(payload['tasks'][0]['uncounted_backends'], [])

    # ----- failure modes -----

    def test_managers_that_cannot_list_degrade_to_an_empty_report(self) -> None:
        sessions = MagicMock()
        sessions.list_records.side_effect = RuntimeError('index locked')
        workspaces = MagicMock()
        workspaces.list_workspaces.side_effect = OSError('disk gone')
        logger = MagicMock()
        payload = collect_usage(sessions, workspaces, TranscriptUsageReader(), logger=logger)
        self.assertEqual(payload['tasks'], [])
        self.assertEqual(logger.exception.call_count, 2)

    def test_no_managers_at_all_is_an_empty_report(self) -> None:
        payload = collect_usage(None, None, TranscriptUsageReader())
        self.assertEqual(payload['tasks'], [])
        self.assertIn('generated_at', payload)

    def test_an_exception_with_no_logger_is_still_survived(self) -> None:
        sessions = MagicMock()
        sessions.list_records.side_effect = RuntimeError('boom')
        workspaces = MagicMock()
        workspaces.list_workspaces.side_effect = RuntimeError('boom')
        self.assertEqual(collect_usage(sessions, workspaces, TranscriptUsageReader())['tasks'], [])

    # ----- the route -----

    def test_the_route_serves_the_report_as_json(self) -> None:
        self._transcript('-work-T1', 'aaaa1111', _turn('m1', out=12, read=88))
        app = Flask(__name__)
        app.config['SESSION_MANAGER'] = self._manager(
            AgentSessionRecord(task_id='T1', task_summary='the task', agent_session_id='aaaa1111'),
        )
        app.config['WORKSPACE_MANAGER'] = self._workspaces()
        register_usage_routes(app)

        response = app.test_client().get('/api/usage')

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['tasks'][0]['task_id'], 'T1')
        self.assertEqual(body['tasks'][0]['usage']['total'], 100)
        self.assertEqual(body['totals']['output'], 12)

    def test_a_second_request_reuses_what_the_first_read(self) -> None:
        self._transcript('-work-T1', 'aaaa1111', _turn('m1', out=1))
        reader = TranscriptUsageReader()
        app = Flask(__name__)
        app.config['SESSION_MANAGER'] = self._manager(
            AgentSessionRecord(task_id='T1', agent_session_id='aaaa1111'),
        )
        register_usage_routes(app, reader=reader)
        client = app.test_client()
        client.get('/api/usage')
        state = next(iter(reader._files.values()))
        client.get('/api/usage')
        self.assertIs(next(iter(reader._files.values())), state)

    # ----- cost and model names -----

    def test_a_task_carries_its_estimated_cost_in_the_three_groups(self) -> None:
        # Opus 5.5 at $4 in / $20 out / $0.20 read: 1M of each.
        self._transcript('-work-T1', 'aaaa1111', _turn(
            'm1', model='claude-opus-5-5', inp=1_000_000, out=1_000_000, read=1_000_000,
        ))
        payload = self._collect(
            self._manager(AgentSessionRecord(task_id='T1', agent_session_id='aaaa1111')),
            self._workspaces(),
        )
        cost = payload['tasks'][0]['usage']['cost']
        self.assertEqual(cost, {'output': 20.0, 'fresh': 4.0, 'cache_read': 0.2, 'total': 24.2})
        self.assertEqual(payload['totals']['cost'], cost)
        self.assertEqual(payload['tasks'][0]['usage']['unpriced_models'], [])

    def test_every_model_is_named_the_way_a_person_reads_it(self) -> None:
        self._transcript(
            '-work-T1', 'aaaa1111',
            _turn('a', out=1, model='claude-opus-5-5'),
            _turn('b', out=1, model='claude-opus-5'),
            _turn('c', out=1, model='claude-haiku-4-5-20251001'),
            _turn('d', out=1, model='claude-made-up-9'),
            _turn('e', out=1, model=''),
        )
        payload = self._collect(
            self._manager(AgentSessionRecord(task_id='T1', agent_session_id='aaaa1111')),
            self._workspaces(),
        )
        labels = {model: slot['label'] for model, slot in payload['tasks'][0]['usage']['models'].items()}
        self.assertEqual(labels, {
            'claude-opus-5-5': 'Opus 5.5',
            'claude-opus-5': 'Opus 5',
            'claude-haiku-4-5-20251001': 'Haiku 4.5',
            'claude-made-up-9': 'claude-made-up-9',
            '': 'unknown',
        })
        # The grand total's models are named too, from the same helper.
        self.assertEqual(payload['totals']['models']['claude-opus-5-5']['label'], 'Opus 5.5')

    def test_a_model_without_a_price_is_named_and_the_task_says_so(self) -> None:
        self._transcript('-work-T1', 'aaaa1111', _turn('a', out=1_000_000, model='claude-made-up-9'))
        payload = self._collect(
            self._manager(AgentSessionRecord(task_id='T1', agent_session_id='aaaa1111')),
            self._workspaces(),
        )
        usage = payload['tasks'][0]['usage']
        self.assertEqual(usage['unpriced_models'], ['claude-made-up-9'])
        self.assertEqual(usage['cost']['total'], 0.0)
        self.assertFalse(usage['models']['claude-made-up-9']['priced'])

    def test_a_model_switched_between_chats_is_one_task_with_two_model_rows(self) -> None:
        self._transcript('-work-T1', 'old0001',
                         _turn('a', model='claude-opus-5-5', out=100, at='2026-10-01T10:00:00Z'))
        self._transcript('-work-T1', 'current',
                         _turn('b', model='claude-sonnet-5-5', out=300, at='2026-10-02T10:00:00Z'))
        payload = self._collect(
            self._manager(AgentSessionRecord(
                task_id='T1', agent_session_id='current', previous_session_ids=['old0001'],
            )),
            self._workspaces(),
        )
        models = payload['tasks'][0]['usage']['models']
        self.assertEqual(sorted(models), ['claude-opus-5-5', 'claude-sonnet-5-5'])
        self.assertEqual(models['claude-opus-5-5']['last_at'], '2026-10-01T10:00:00Z')
        self.assertEqual(models['claude-sonnet-5-5']['first_at'], '2026-10-02T10:00:00Z')

    def test_the_report_says_which_day_its_prices_are_from(self) -> None:
        payload = self._collect(self._manager(), self._workspaces())
        self.assertEqual(payload['pricing_as_of'], PRICES_AS_OF)

    # ----- when a task was created, and last used -----

    def test_a_task_says_when_it_was_created_and_when_it_last_ran(self) -> None:
        self._transcript('-work-T1', 'aaaa1111',
                         _turn('a', out=1, at='2026-10-02T10:00:00Z'),
                         _turn('b', out=1, at='2026-10-05T10:00:00Z'))
        # 2026-10-01T00:00:00Z
        created = 1_790_812_800
        payload = self._collect(
            self._manager(AgentSessionRecord(
                task_id='T1', agent_session_id='aaaa1111', created_at_epoch=created,
            )),
            self._workspaces(),
        )
        task = payload['tasks'][0]
        self.assertEqual(task['created_at'], '2026-10-01T00:00:00Z')
        self.assertEqual(task['usage']['last_at'], '2026-10-05T10:00:00Z')

    def test_the_earliest_stamp_kato_kept_is_the_creation_date(self) -> None:
        self._transcript('-work-T1', 'aaaa1111', _turn('a', out=1))
        payload = self._collect(
            self._manager(AgentSessionRecord(
                task_id='T1', agent_session_id='aaaa1111', created_at_epoch=1_790_900_000,
            )),
            self._workspaces({
                'task_id': 'T1', 'task_summary': '', 'status': 'active',
                'agent_session_id': 'aaaa1111', 'created_at_epoch': 1_790_812_800,
            }),
        )
        self.assertEqual(payload['tasks'][0]['created_at'], '2026-10-01T00:00:00Z')

    def test_with_no_creation_stamp_the_first_turn_stands_in(self) -> None:
        self._transcript('-work-T1', 'aaaa1111', _turn('a', out=1, at='2026-10-02T10:00:00Z'))
        payload = self._collect(
            self._manager(),
            self._workspaces({
                'task_id': 'T1', 'task_summary': '', 'status': 'active',
                'agent_session_id': 'aaaa1111', 'created_at_epoch': 'garbage',
            }),
        )
        self.assertEqual(payload['tasks'][0]['created_at'], '2026-10-02T10:00:00Z')

    # ----- deleted tasks: kept as numbers, reported apart -----

    @staticmethod
    def _kept(task_id, *, out, read=0, summary='gone', deleted_at='2026-10-10T00:00:00Z'):
        total = out + read
        return {
            'task_id': task_id, 'summary': summary, 'status': 'done', 'chats': 2,
            'uncounted_backends': [], 'created_at': '2026-09-01T00:00:00Z',
            'deleted_at': deleted_at,
            'usage': {
                'input': 0, 'output': out, 'cache_read': read, 'cache_write': 0,
                'total': total, 'turns': 3, 'first_at': '2026-09-01T01:00:00Z',
                'last_at': '2026-09-09T01:00:00Z',
                'cost': {'output': 1.0, 'fresh': 0.0, 'cache_read': 0.5, 'total': 1.5},
                'unpriced_models': [],
                'models': {'claude-opus-5-5': {
                    'input': 0, 'output': out, 'cache_read': read, 'cache_write': 0,
                    'total': total, 'turns': 3, 'first_at': '2026-09-01T01:00:00Z',
                    'last_at': '2026-09-09T01:00:00Z', 'priced': True,
                    'cost': {'output': 1.0, 'fresh': 0.0, 'cache_read': 0.5, 'total': 1.5},
                }},
            },
        }

    def test_deleted_tasks_are_listed_apart_and_never_in_the_live_totals(self) -> None:
        self._transcript('-work-T1', 'aaaa1111', _turn('a', out=10, read=90))
        payload = collect_usage(
            self._manager(AgentSessionRecord(task_id='T1', agent_session_id='aaaa1111')),
            self._workspaces(), TranscriptUsageReader(),
            deleted=[self._kept('OLD-1', out=5, read=495), self._kept('OLD-2', out=700)],
        )
        self.assertEqual([t['task_id'] for t in payload['tasks']], ['T1'])
        self.assertEqual(payload['totals']['total'], 100)
        # Biggest first, like the live list.
        self.assertEqual([t['task_id'] for t in payload['deleted_tasks']], ['OLD-2', 'OLD-1'])
        self.assertEqual(payload['deleted_tasks'][0]['deleted_at'], '2026-10-10T00:00:00Z')
        self.assertEqual(payload['totals_with_deleted']['total'], 100 + 500 + 700)
        self.assertEqual(payload['totals_with_deleted']['turns'], 1 + 3 + 3)
        # Their kept cost is part of the total with them, and only of that one.
        live_cost = payload['totals']['cost']['total']
        self.assertAlmostEqual(payload['totals_with_deleted']['cost']['total'], live_cost + 3.0)
        self.assertLess(live_cost, 0.01)

    def test_with_nothing_deleted_both_totals_agree(self) -> None:
        self._transcript('-work-T1', 'aaaa1111', _turn('a', out=10))
        payload = self._collect(
            self._manager(AgentSessionRecord(task_id='T1', agent_session_id='aaaa1111')),
            self._workspaces(),
        )
        self.assertEqual(payload['deleted_tasks'], [])
        self.assertEqual(payload['totals_with_deleted'], payload['totals'])

    def test_a_kept_task_is_named_again_when_it_is_read_back(self) -> None:
        payload = collect_usage(
            None, None, TranscriptUsageReader(), deleted=[self._kept('OLD-1', out=5)],
        )
        slot = payload['deleted_tasks'][0]['usage']['models']['claude-opus-5-5']
        self.assertEqual(slot['label'], 'Opus 5.5')
        self.assertEqual(payload['totals_with_deleted']['models']['claude-opus-5-5']['label'], 'Opus 5.5')

    def test_a_damaged_kept_task_still_has_every_field_the_page_reads(self) -> None:
        payload = collect_usage(
            None, None, TranscriptUsageReader(),
            deleted=[{'task_id': 'OLD-9', 'usage': {'output': 4, 'total': 4}}],
        )
        task = payload['deleted_tasks'][0]
        self.assertEqual(task['usage']['output'], 4)
        for field in ('input', 'cache_read', 'cache_write', 'turns'):
            self.assertEqual(task['usage'][field], 0)
        self.assertEqual(task['usage']['models'], {})
        self.assertEqual((task['summary'], task['created_at'], task['deleted_at']), ('', '', ''))
        self.assertEqual(task['chats'], 0)

    def test_the_route_serves_what_was_kept(self) -> None:
        history = Path(self._tmp.name) / 'usage_history.json'
        history.write_text(json.dumps([self._kept('OLD-1', out=5)]), encoding='utf-8')
        app = Flask(__name__)
        register_usage_routes(app)
        with patch.dict(os.environ, {'KATO_USAGE_HISTORY_PATH': str(history)}):
            body = app.test_client().get('/api/usage').get_json()
        self.assertEqual([t['task_id'] for t in body['deleted_tasks']], ['OLD-1'])
        self.assertEqual(body['totals_with_deleted']['output'], 5)
        self.assertEqual(body['totals']['output'], 0)


class _Manager:
    """A session manager whose delete removes the transcript, as the real one does."""

    def __init__(self, record, transcript: Path, *, fail: bool = False) -> None:
        self._records = [record]
        self._transcript = transcript
        self._fail = fail

    def list_records(self):
        return list(self._records)

    def terminate_session(self, task_id, *, remove_record=False):
        if self._fail:
            raise RuntimeError('subprocess gone')
        self._transcript.unlink()
        self._records = []


class DeletingATaskKeepsWhatItUsedTests(unittest.TestCase):
    """The delete route, with real transcripts: the figures outlive the transcripts."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-usage-del-')
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.projects = root / 'projects'
        (self.projects / '-work-T1').mkdir(parents=True)
        self.transcript = self.projects / '-work-T1' / 'aaaa1111.jsonl'
        self.transcript.write_text(
            _turn('a', out=40, read=60, at='2026-10-02T10:00:00Z'), encoding='utf-8',
        )
        self.history = root / 'usage_history.json'
        env = patch.dict(os.environ, {
            'CLAUDE_SESSIONS_ROOT': str(self.projects),
            'KATO_USAGE_HISTORY_PATH': str(self.history),
            'KATO_FORGOTTEN_TASKS_PATH': str(root / 'forgotten.json'),
        })
        env.start()
        self.addCleanup(env.stop)
        self.workspaces = MagicMock()
        self.workspaces.list_workspaces.return_value = []
        self.workspaces.workspace_path.return_value = root / 'no-such-workspace'

    def _app(self, manager):
        from kato_webserver.app import create_app
        return create_app(session_manager=manager, workspace_manager=self.workspaces)

    def _manager(self, **kwargs):
        return _Manager(
            AgentSessionRecord(
                task_id='T1', task_summary='the task', agent_session_id='aaaa1111',
                created_at_epoch=1_790_812_800,
            ),
            self.transcript, **kwargs,
        )

    def test_the_task_leaves_the_list_but_not_the_history(self) -> None:
        client = self._app(self._manager()).test_client()

        self.assertEqual(client.get('/api/usage').get_json()['tasks'][0]['task_id'], 'T1')
        self.assertEqual(client.delete('/api/sessions/T1/workspace').status_code, 200)
        body = client.get('/api/usage').get_json()

        self.assertFalse(self.transcript.exists())
        self.assertEqual(body['tasks'], [])
        self.assertEqual(body['totals']['total'], 0)
        [kept] = body['deleted_tasks']
        self.assertEqual(kept['task_id'], 'T1')
        self.assertEqual(kept['summary'], 'the task')
        self.assertEqual(kept['created_at'], '2026-10-01T00:00:00Z')
        self.assertTrue(kept['deleted_at'])
        self.assertEqual(kept['usage']['total'], 100)
        self.assertEqual(kept['usage']['last_at'], '2026-10-02T10:00:00Z')
        self.assertEqual(body['totals_with_deleted']['total'], 100)

    def test_only_numbers_are_kept_never_the_conversation(self) -> None:
        self.transcript.write_text(
            '{"type":"user","message":{"content":"my secret prompt"}}\n'
            + _turn('a', out=1), encoding='utf-8',
        )
        self._app(self._manager()).test_client().delete('/api/sessions/T1/workspace')
        self.assertNotIn('secret', self.history.read_text(encoding='utf-8'))

    def test_a_delete_that_failed_to_remove_the_transcripts_keeps_nothing(self) -> None:
        # The transcripts are still there, still counted under the live task;
        # a kept copy as well would count the same tokens twice.
        client = self._app(self._manager(fail=True)).test_client()

        self.assertEqual(client.delete('/api/sessions/T1/workspace').status_code, 500)

        self.assertFalse(self.history.exists())
        body = client.get('/api/usage').get_json()
        self.assertEqual(body['deleted_tasks'], [])
        self.assertEqual(body['tasks'][0]['usage']['total'], 100)

    def test_deleting_a_task_that_used_nothing_keeps_nothing(self) -> None:
        self.transcript.write_text('', encoding='utf-8')
        response = self._app(self._manager()).test_client().delete('/api/sessions/T1/workspace')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.history.exists())

    def test_a_snapshot_that_cannot_be_read_never_stops_the_delete(self) -> None:
        manager = self._manager()
        manager.list_records = MagicMock(side_effect=RuntimeError('index locked'))
        app = self._app(manager)
        with self.assertLogs(app.logger, level='ERROR'):
            response = app.test_client().delete('/api/sessions/T1/workspace')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.history.exists())

    def test_a_history_that_cannot_be_written_never_stops_the_delete(self) -> None:
        with patch('kato_webserver.usage_routes.record_deleted_task_usage',
                   side_effect=OSError('disk full')):
            response = self._app(self._manager()).test_client().delete('/api/sessions/T1/workspace')
        self.assertEqual(response.status_code, 200)


if __name__ == '__main__':
    unittest.main()
