"""The routes behind "Ask the plan's chat" on a plan sub-task's question.

The real Flask app over the real ``ParentQuestions``; only the chat is a
stand-in, answering at once, and the background thread is run inline unless a
test holds it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from workspace_core_lib.workspace_core_lib import WorkspaceCoreLib

from kato_core_lib.data_layers.service.plan_queue.parent_question import ParentQuestions
from kato_webserver.app import create_app

QUESTION = {'header': 'Per-IP key', 'question': 'Which way?', 'options': [{'label': 'Shared bucket'}]}


class _Queue(object):
    def plan_for_task(self, task_id):
        return SimpleNamespace(source_task_id='UNA-3267') if task_id == 'UNA-3274' else None


class _Chat(object):
    def ask(self, task_id, message, *, cancel_event):
        return f'{task_id} says: shared bucket.'


class PlanQuestionRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.held = []
        self.inline = True
        self.questions = ParentQuestions(queue=_Queue(), question=_Chat(), start=self._start)
        from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
        manager = ClaudeSessionManager(state_dir=str(root / 'sessions'))
        self.addCleanup(manager.shutdown)
        workspaces = WorkspaceCoreLib(root=str(root / 'workspaces'), max_parallel_tasks=1).workspaces
        self.client = create_app(
            session_manager=manager, workspace_manager=workspaces,
            agent_service=SimpleNamespace(plan_questions=self.questions),
        ).test_client()

    def _start(self, run) -> None:
        if self.inline:
            run()
        else:
            self.held.append(run)

    def test_a_sub_task_names_its_parent_and_has_no_question_yet(self) -> None:
        body = self.client.get('/api/sessions/UNA-3274/plan-question').get_json()
        self.assertEqual(body, {'parent': 'UNA-3267', 'question': None})
        self.assertEqual(self.client.get('/api/sessions/UNA-1/plan-question').get_json()['parent'], '')

    def test_asked_then_answered(self) -> None:
        response = self.client.post('/api/sessions/UNA-3274/plan-question', json={'questions': [QUESTION]})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()['parent'], 'UNA-3267')
        question = self.client.get('/api/sessions/UNA-3274/plan-question').get_json()['question']
        self.assertEqual((question['status'], question['answer']), ('answered', 'UNA-3267 says: shared bucket.'))

    def test_refusals_carry_their_reason(self) -> None:
        cases = [
            ('UNA-3274', {'questions': []}, 400),
            ('UNA-3274', ['not', 'an', 'object'], 400),
            ('UNA-1', {'questions': [QUESTION]}, 404),
        ]
        for task_id, body, status in cases:
            with self.subTest(task_id=task_id, body=body):
                response = self.client.post(f'/api/sessions/{task_id}/plan-question', json=body)
                self.assertEqual(response.status_code, status)
                self.assertTrue(response.get_json()['error'])

    def test_one_at_a_time_and_cancel_stops_the_wait(self) -> None:
        self.inline = False
        self.client.post('/api/sessions/UNA-3274/plan-question', json={'questions': [QUESTION]})
        again = self.client.post('/api/sessions/UNA-3274/plan-question', json={'questions': [QUESTION]})
        self.assertEqual(again.status_code, 409)
        cancelled = self.client.delete('/api/sessions/UNA-3274/plan-question').get_json()['question']
        self.assertEqual(cancelled['status'], 'cancelled')

    def test_without_the_service_every_route_says_so(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
        manager = ClaudeSessionManager(state_dir=str(Path(tmp.name) / 'sessions'))
        self.addCleanup(manager.shutdown)
        client = create_app(session_manager=manager, workspace_manager=None,
                            agent_service=SimpleNamespace(plan_questions=None)).test_client()
        for method in (client.get, client.post, client.delete):
            with self.subTest(method=method.__name__):
                self.assertEqual(method('/api/sessions/UNA-3274/plan-question').status_code, 503)


if __name__ == '__main__':
    unittest.main()
