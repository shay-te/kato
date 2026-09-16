"""``POST /api/sessions/<task>/chats/handoff`` — a new chat that starts from a summary.

The context meter's "New chat from a summary": once the agent's summary turn
ends, this reads the summary back (live session first, transcript on disk
otherwise), detaches the current chat the way "New chat" does, and hands the
composer the opening message. With no summary nothing is detached — a new chat
without its context is exactly what the operator asked to avoid.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kato_webserver.app import create_app

SUMMARY_TEXT = '<kato-handoff>\nGoal: ship the fix\nNext: run the tests\n</kato-handoff>'


def _assistant(text):
    return SimpleNamespace(
        event_type='assistant',
        raw={'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': text}]}},
    )


class _Manager:
    def __init__(self, *, events=None, live=True) -> None:
        self.record = SimpleNamespace(
            task_id='T1', agent_session_id='sess-old', previous_session_ids=[],
            agent_backend='claude',
        )
        self.session = SimpleNamespace(
            is_alive=True, recent_events=lambda: list(events or []),
        ) if live else None
        self.new_chats: list[str] = []

    def list_records(self):
        return [self.record]

    def get_record(self, task_id):
        return self.record if task_id == 'T1' else None

    def get_session(self, task_id):  # noqa: ARG002
        return self.session

    def start_new_chat(self, task_id, *, agent_session_id=''):  # noqa: ARG002
        self.new_chats.append(task_id)
        self.record.previous_session_ids.append(self.record.agent_session_id)
        self.record.agent_session_id = ''
        return self.record


class ChatHandoffRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.dict(os.environ, {'HOME': str(Path(tmp.name) / 'home')})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, manager, agent_service=None):
        app = create_app(session_manager=manager, agent_service=agent_service)
        return app.test_client().post('/api/sessions/T1/chats/handoff', json={})

    def test_the_new_chat_opens_with_the_agents_summary(self) -> None:
        manager = _Manager(events=[_assistant('Sure.'), _assistant(SUMMARY_TEXT)])
        response = self._post(manager)
        self.assertEqual(response.status_code, 200, response.get_json())
        body = response.get_json()
        self.assertEqual(body['summary'], 'Goal: ship the fix\nNext: run the tests')
        self.assertTrue(body['opening_message'].endswith(body['summary']))
        self.assertEqual(manager.new_chats, ['T1'])
        self.assertEqual(body['previous_session_ids'], ['sess-old'])
        self.assertEqual(body['agent_session_id'], '')

    def test_without_a_summary_the_chat_is_left_alone(self) -> None:
        manager = _Manager(events=[_assistant('I did not write one.')])
        response = self._post(manager)
        self.assertEqual(response.status_code, 409)
        self.assertIn('handoff summary', response.get_json()['error'])
        self.assertEqual(manager.new_chats, [])

    def test_an_exited_session_is_read_from_its_transcript(self) -> None:
        manager = _Manager(live=False)
        records = [_assistant(SUMMARY_TEXT).raw]
        with patch('kato_webserver.app._resolve_agent_session_id', return_value='sess-old'), \
                patch(
                    'claude_core_lib.claude_core_lib.session.history.load_history_events',
                    return_value=records,
                ) as load:
            response = self._post(manager)
        self.assertEqual(response.status_code, 200, response.get_json())
        load.assert_called_once_with('sess-old')
        self.assertEqual(response.get_json()['summary'], 'Goal: ship the fix\nNext: run the tests')

    def test_a_live_session_with_no_events_falls_back_to_the_transcript(self) -> None:
        manager = _Manager(events=[])
        with patch('kato_webserver.app._resolve_agent_session_id', return_value='sess-old'), \
                patch(
                    'claude_core_lib.claude_core_lib.session.history.load_history_events',
                    return_value=[_assistant(SUMMARY_TEXT).raw],
                ):
            self.assertEqual(self._post(manager).status_code, 200)

    def test_no_transcript_either_is_no_summary(self) -> None:
        manager = _Manager(live=False)
        with patch('kato_webserver.app._resolve_agent_session_id', return_value=''):
            self.assertEqual(self._post(manager).status_code, 409)
        self.assertEqual(manager.new_chats, [])

    def test_a_running_comment_fix_blocks_the_switch(self) -> None:
        manager = _Manager(events=[_assistant(SUMMARY_TEXT)])
        agent_service = SimpleNamespace(comments=SimpleNamespace(
            list_task_comments=lambda task_id: [{'kato_status': 'in_progress'}],
        ))
        response = self._post(manager, agent_service)
        self.assertEqual(response.status_code, 409)
        self.assertIn('review comment', response.get_json()['error'])
        self.assertEqual(manager.new_chats, [])

    def test_an_unknown_task_is_a_404(self) -> None:
        app = create_app(session_manager=_Manager())
        response = app.test_client().post('/api/sessions/NOPE/chats/handoff', json={})
        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
