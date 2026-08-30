"""``POST /api/sessions/<id>/chats/<chat_id>/name`` — name a chat.

Purely an operator label: the agent never sees it and no behaviour keys on it.
The list derived its label from the first user message before, which is a
reasonable guess and a poor name.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kato_webserver.app import create_app


def _record(active='chat-active', previous=('chat-old',)):
    return SimpleNamespace(
        task_id='T1',
        agent_backend='claude',
        agent_session_id=active,
        previous_session_ids=list(previous),
    )


class _Manager:
    def __init__(self, record=None) -> None:
        self._record = record

    def list_records(self):
        return [self._record] if self._record else []

    def get_record(self, task_id):  # noqa: ARG002
        return self._record

    def get_session(self, task_id):  # noqa: ARG002
        return None


class ChatRenameRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        env = patch.dict(os.environ, {
            'KATO_CHAT_NAMES_PATH': str(Path(self._td.name) / 'chat_names.json'),
        })
        env.start()
        self.addCleanup(env.stop)
        self.app = create_app(session_manager=_Manager(_record()))
        self.client = self.app.test_client()

    def _rename(self, chat_id, name):
        return self.client.post(
            f'/api/sessions/T1/chats/{chat_id}/name', json={'name': name},
        )

    def _chats(self):
        return self.client.get('/api/sessions/T1/chats').get_json()['chats']

    def test_naming_the_active_chat(self) -> None:
        response = self._rename('chat-active', 'The flaky test hunt')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['name'], 'The flaky test hunt')

    def test_the_name_comes_back_on_the_chats_list(self) -> None:
        self._rename('chat-active', 'Renamed')
        row = next(c for c in self._chats() if c['active'])
        self.assertEqual(row['name'], 'Renamed')

    def test_a_never_renamed_chat_reports_an_empty_name(self) -> None:
        # The client falls back to the first-user-message preview.
        self.assertTrue(all(c['name'] == '' for c in self._chats()))

    def test_a_PREVIOUS_chat_can_be_named_too(self) -> None:
        # Detached chats are the ones that most need names — they are the
        # ones you come back to.
        self.assertEqual(self._rename('chat-old', 'Yesterday').status_code, 200)
        row = next(c for c in self._chats() if not c['active'])
        self.assertEqual(row['name'], 'Yesterday')

    def test_an_empty_name_clears_it(self) -> None:
        self._rename('chat-active', 'temporary')
        self.assertEqual(self._rename('chat-active', '').get_json()['name'], '')
        row = next(c for c in self._chats() if c['active'])
        self.assertEqual(row['name'], '')

    def test_a_chat_from_ANOTHER_task_is_refused(self) -> None:
        # Without this, any session id on the machine could be labelled
        # through whichever task the operator has open — wrong, and a way to
        # probe for ids.
        response = self._rename('someone-elses-chat', 'mine now')
        self.assertEqual(response.status_code, 404)
        self.assertIn('does not belong', response.get_json()['error'])

    def test_an_unknown_task_is_a_404(self) -> None:
        app = create_app(session_manager=_Manager(None))
        response = app.test_client().post(
            '/api/sessions/nope/chats/chat-active/name', json={'name': 'x'},
        )
        self.assertEqual(response.status_code, 404)

    def test_the_name_survives_a_restart(self) -> None:
        # It is an operator label on a conversation they will come back to —
        # losing it on restart would make it worthless.
        self._rename('chat-active', 'Persisted')
        reborn = create_app(session_manager=_Manager(_record())).test_client()
        row = next(
            c for c in reborn.get('/api/sessions/T1/chats').get_json()['chats']
            if c['active']
        )
        self.assertEqual(row['name'], 'Persisted')


if __name__ == '__main__':
    unittest.main()
