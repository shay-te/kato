"""The "Hand over" routes, over the real ``TaskHandover`` and kato's real task
services; only the tracker's HTTP client is an in-memory board (shared with
``tests/test_task_handover.py``) and the session manager a plain stand-in."""

from __future__ import annotations

import logging
import tempfile
import types
import unittest
import uuid
from pathlib import Path

from kato_core_lib.data_layers.data.fields import TaskTags
from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
from kato_core_lib.data_layers.service.local_task_routing import (
    LocalAwareTaskService,
    LocalAwareTaskStateService,
)
from kato_core_lib.data_layers.service.task_handover import LOCAL_TASK_REASON, TaskHandover
from kato_webserver.app import create_app
from kato_webserver.handover_routes import PLAN_SOURCE_REASON, STARTING_REASON
from kato_webserver.task_agent_stop import NO_AGENT, STOPPED
from local_task_core_lib.local_task_core_lib.store import LocalTaskStore
from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan, PlanMode
from plan_queue_core_lib.plan_queue_core_lib.service import PlanQueueService
from plan_queue_core_lib.plan_queue_core_lib.store import PlanQueueStore
from plan_queue_core_lib.plan_queue_core_lib.tests.fakes import (
    FakeProbe,
    FakeProgress,
    FakeProposer,
    FakeWording,
)
from tests.test_task_handover import ME, OPEN, STARTED, _Board, _Chat, _config


class _Manager(object):
    """Sessions by task: a record means it can be stopped; a chat may be working."""

    def __init__(self) -> None:
        self.records: set[str] = set()
        self.chats: dict[str, _Chat] = {}
        self.terminated: list[str] = []
        self.terminate_raises = ''

    def list_records(self):
        return [types.SimpleNamespace(task_id=task_id) for task_id in sorted(self.records)]

    def get_record(self, task_id):
        return types.SimpleNamespace(task_id=task_id) if task_id in self.records else None

    def get_session(self, task_id):
        return self.chats.get(task_id)

    def terminate_session(self, task_id, remove_record=True):
        if self.terminate_raises:
            raise RuntimeError(self.terminate_raises)
        self.terminated.append(task_id)
        self.records.discard(task_id)


class _Loops(object):
    def __init__(self) -> None:
        self.stopped: list[tuple[str, str]] = []

    def stop(self, task_id, *, reason):
        self.stopped.append((task_id, reason))


class HandoverRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = LocalTaskStore(Path(tmp.name) / 'local_tasks.json')
        self.board = _Board()
        self.board.say('UNA-1', STARTED)
        access = TaskDataAccess(_config(), self.board)
        self.manager = _Manager()
        self.loops = _Loops()
        handover = TaskHandover(
            task_service=LocalAwareTaskService(_config(), access, self.store),
            task_state_service=LocalAwareTaskStateService(_config(), access, self.store),
            session_manager=self.manager,
            signed_by=lambda: "Shay's kato",
        )
        agent = types.SimpleNamespace(handover=handover, review_loops=self.loops)
        self.client = create_app(session_manager=self.manager, agent_service=agent).test_client()
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def test_a_tracker_ticket_is_available(self) -> None:
        response = self.client.get('/api/sessions/UNA-1/handover')
        self.assertEqual(response.get_json(), {'available': True, 'reason': '', 'remove_here_reason': ''})
        self.assertEqual(self.board.calls, [])  # no tracker call to decide

    def test_a_local_task_is_not(self) -> None:
        local_id = self.store.create('local', 'details').id
        self.assertEqual(self.client.get(f'/api/sessions/{local_id}/handover').get_json(),
                         {'available': False, 'reason': LOCAL_TASK_REASON, 'remove_here_reason': ''})
        response = self.client.get(f'/api/sessions/{local_id}/handover/users')
        self.assertEqual(response.status_code, 400)
        post = self.client.post(f'/api/sessions/{local_id}/handover', json={'handle': 'jane'})
        self.assertEqual(post.status_code, 400)

    def test_the_users_come_from_the_tracker(self) -> None:
        response = self.client.get('/api/sessions/UNA-1/handover/users')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['users'][1], {'handle': 'jane', 'name': 'Jane Doe'})

    def test_a_tracker_that_does_not_answer_is_a_502(self) -> None:
        self.board.refuse['users'] = 'YouTrack is down'
        response = self.client.get('/api/sessions/UNA-1/handover/users')
        self.assertEqual(response.status_code, 502)
        self.assertIn('YouTrack is down', response.get_json()['error'])

    def test_hand_over_end_to_end_stops_the_agent_through_the_chat_s_stop(self) -> None:
        self.manager.records.add('UNA-1')
        response = self.client.post('/api/sessions/UNA-1/handover', json={'handle': 'jane', 'name': 'Jane Doe'})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['status'], 'handed_over')
        self.assertTrue(body['landed'])
        self.assertEqual(body['steps'][-1], {'step': 'stop', 'ok': True, 'detail': STOPPED})
        self.assertEqual(self.manager.terminated, ['UNA-1'])
        self.assertEqual(self.loops.stopped, [('UNA-1', 'the task was handed over')])
        self.assertEqual((self.board.assignee['UNA-1'], self.board.state['UNA-1']), ('jane', OPEN))
        self.assertIn(TaskTags.WAIT_EDITING, self.board.tags['UNA-1'])

    def test_no_agent_running_is_said_not_failed(self) -> None:
        body = self.client.post('/api/sessions/UNA-1/handover', json={'handle': 'jane'}).get_json()
        self.assertEqual(body['steps'][-1], {'step': 'stop', 'ok': True, 'detail': NO_AGENT})

    def test_an_agent_that_will_not_stop_is_reported(self) -> None:
        self.manager.records.add('UNA-1')
        self.manager.terminate_raises = 'stuck'
        body = self.client.post('/api/sessions/UNA-1/handover', json={'handle': 'jane'}).get_json()
        self.assertEqual(body['status'], 'handed_over_with_problems')
        self.assertEqual(body['steps'][-1], {'step': 'stop', 'ok': False, 'detail': 'stuck'})

    def test_refused_while_the_agent_works(self) -> None:
        self.manager.chats['UNA-1'] = _Chat(working=True)
        response = self.client.post('/api/sessions/UNA-1/handover', json={'handle': 'jane'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.board.assignee['UNA-1'], ME)

    def test_no_one_picked_is_a_400(self) -> None:
        for body in ({}, {'handle': ' '}, None):
            with self.subTest(body=body):
                response = self.client.post('/api/sessions/UNA-1/handover', json=body)
                self.assertEqual(response.status_code, 400)

    def test_only_an_explicit_false_hands_it_over_untagged(self) -> None:
        self.board.tags['UNA-1'].append(TaskTags.WAIT_EDITING)
        body = self.client.post('/api/sessions/UNA-1/handover',
                                json={'handle': 'jane', 'wait_for_them': False}).get_json()
        self.assertTrue(body['landed'])
        self.assertEqual(body['steps'][1], {'step': 'untag', 'ok': True, 'detail': ''})
        self.assertNotIn(TaskTags.WAIT_EDITING, self.board.tags['UNA-1'])

    def test_anything_but_false_tags_it(self) -> None:
        for wait in (True, 'false', 0, None):
            with self.subTest(wait=wait):
                self.board.tags['UNA-1'] = []
                self.board.assignee['UNA-1'] = ME
                body = self.client.post('/api/sessions/UNA-1/handover',
                                        json={'handle': 'jane', 'wait_for_them': wait}).get_json()
                self.assertEqual(body['steps'][1]['step'], 'tag')
                self.assertIn(TaskTags.WAIT_EDITING, self.board.tags['UNA-1'])

    def test_a_failed_assignment_is_a_502_with_its_steps(self) -> None:
        self.board.refuse['assign'] = 'Unknown user'
        response = self.client.post('/api/sessions/UNA-1/handover', json={'handle': 'jane'})
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.get_json()['landed'])
        self.assertEqual(response.get_json()['steps'], [{'step': 'assign', 'ok': False, 'detail': 'Unknown user'}])


class RemoveHereOfferTests(unittest.TestCase):
    """The picker's "also delete it from my tasks" is not offered for a task a
    plan THIS kato drives was split from — the delete would take the plan's
    sub-tasks with it. Against a real plan queue reading real saved plans."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.plans = PlanQueueStore(Path(tmp.name) / 'plans')

    def _client(self, *plans: Plan):
        for plan in plans:
            self.plans.save(plan)
        queue = PlanQueueService(
            store=self.plans, proposer=FakeProposer(''), wording=FakeWording(),
            progress=FakeProgress(), probe=FakeProbe(),
        )
        self.addCleanup(queue.shutdown)
        agent = types.SimpleNamespace(handover=None, plan_queue=queue)
        return create_app(session_manager=_Manager(), agent_service=agent).test_client()

    def _reason(self, client, task_id: str = 'UNA-1') -> str:
        return client.get(f'/api/sessions/{task_id}/handover').get_json()['remove_here_reason']

    def test_a_plan_this_kato_drives_keeps_its_source_here(self) -> None:
        for mode in (PlanMode.LOCAL, PlanMode.TRACKER):
            with self.subTest(mode=mode):
                client = self._client(Plan(plan_id=uuid.uuid4().hex, source_task_id='UNA-1', mode=mode))
                self.assertEqual(self._reason(client), PLAN_SOURCE_REASON)
                self.assertEqual(self._reason(client, 'una-1'), PLAN_SOURCE_REASON)  # ids ignore case
                self.assertEqual(self._reason(client, 'UNA-2'), '')

    def test_a_plan_another_kato_drives_is_not_this_kato_s_to_delete(self) -> None:
        client = self._client(Plan(plan_id=uuid.uuid4().hex, source_task_id='UNA-1', mode=PlanMode.FOLLOWED))
        self.assertEqual(self._reason(client), '')

    def test_no_plan_queue_offers_it(self) -> None:
        client = create_app(
            session_manager=_Manager(), agent_service=types.SimpleNamespace(handover=None),
        ).test_client()
        self.assertEqual(self._reason(client), '')


class HandoverBeforeKatoIsReadyTests(unittest.TestCase):
    def test_every_route_says_kato_is_still_starting(self) -> None:
        client = create_app(session_manager=_Manager(), agent_service=None).test_client()
        self.assertEqual(client.get('/api/sessions/UNA-1/handover').get_json(),
                         {'available': False, 'reason': STARTING_REASON, 'remove_here_reason': ''})
        self.assertEqual(client.get('/api/sessions/UNA-1/handover/users').status_code, 503)
        self.assertEqual(client.post('/api/sessions/UNA-1/handover', json={'handle': 'j'}).status_code, 503)


class StopRouteSharedHelperTests(unittest.TestCase):
    """The chat's Stop goes through the same ``stop_task_agent``."""

    def test_stop_stops_the_loop_and_the_agent_and_404s_without_a_session(self) -> None:
        manager, loops = _Manager(), _Loops()
        client = create_app(
            session_manager=manager, agent_service=types.SimpleNamespace(review_loops=loops),
        ).test_client()
        self.assertEqual(client.post('/api/sessions/UNA-1/stop').status_code, 404)
        self.assertEqual(loops.stopped, [('UNA-1', 'the chat was stopped')])
        manager.records.add('UNA-1')
        self.assertEqual(client.post('/api/sessions/UNA-1/stop').get_json(), {'status': 'stopped'})
        self.assertEqual(manager.terminated, ['UNA-1'])

    def test_a_loop_that_fails_to_stop_does_not_stop_the_stop(self) -> None:
        manager = _Manager()
        manager.records.add('UNA-1')

        def broken(task_id, *, reason):
            raise RuntimeError('loop store gone')

        client = create_app(
            session_manager=manager, agent_service=types.SimpleNamespace(review_loops=types.SimpleNamespace(stop=broken)),
        ).test_client()
        logging.disable(logging.CRITICAL)
        try:
            self.assertEqual(client.post('/api/sessions/UNA-1/stop').status_code, 200)
        finally:
            logging.disable(logging.NOTSET)
        self.assertEqual(manager.terminated, ['UNA-1'])


if __name__ == '__main__':
    unittest.main()
