"""A Codex chat comes back after kato restarts — end to end.

The pieces were verified in isolation (the CLI writes a rollout; the reader
parses one), which is not the same as the chat actually reappearing. This
drives the REAL SSE stream the browser connects to, against a REAL rollout
file, and asserts the operator's prompt and the agent's reply come back.

Nothing is stubbed except the clock-free bits: the record is a real
AgentSessionRecord, the rollout is a real file in the CLI's real layout, and
the frames are the real wire frames.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent_core_lib.agent_core_lib.session.record import AgentSessionRecord
from kato_webserver import app as app_module


THREAD_ID = '019e5dc9-e2ac-7fc3-a6cf-2c8b135f1b9c'


def _write_rollout(home: Path, thread_id: str, turns: list[tuple[str, str]]):
    day = home / 'sessions' / '2026' / '05' / '25'
    day.mkdir(parents=True, exist_ok=True)
    path = day / f'rollout-2026-05-25T09-19-32-{thread_id}.jsonl'
    with path.open('w', encoding='utf-8') as handle:
        # The preamble the CLI injects on every session — must NOT surface.
        handle.write(json.dumps({
            'type': 'response_item',
            'payload': {
                'type': 'message', 'role': 'developer',
                'content': [{'type': 'input_text',
                             'text': '<permissions instructions>'}],
            },
        }) + '\n')
        for role, text in turns:
            key = 'input_text' if role == 'user' else 'output_text'
            handle.write(json.dumps({
                'type': 'response_item',
                'payload': {
                    'type': 'message', 'role': role,
                    'content': [{'type': key, 'text': text}],
                },
            }) + '\n')
    return path


class CodexChatReplaysAfterRestartTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-codex-restart-')
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        _write_rollout(self.home, THREAD_ID, [
            ('user', 'review my changes'),
            ('assistant', 'Looks good — one nit in auth.py.'),
        ])
        # A restart: the record is on disk, no live session exists.
        self.record = AgentSessionRecord(
            task_id='UNA-1', agent_backend='codex',
            agent_session_id=THREAD_ID,
        )
        self.manager = MagicMock()
        self.manager.get_record.return_value = self.record
        self.manager.get_session.return_value = None

    def _frames(self):
        env = dict(os.environ, CODEX_HOME=str(self.home))
        with patch.dict(os.environ, env, clear=False):
            return list(app_module._replay_history(self.record, THREAD_ID))

    def test_the_prompt_comes_back(self) -> None:
        blob = ''.join(self._frames())
        self.assertIn('review my changes', blob)

    def test_the_reply_comes_back(self) -> None:
        blob = ''.join(self._frames())
        self.assertIn('Looks good', blob)

    def test_they_are_sent_as_history_frames(self) -> None:
        # History, not live events — otherwise a replayed reply would set the
        # in-flight indicator and never clear it.
        for frame in self._frames():
            self.assertIn('session_history_event', frame)

    def test_the_injected_preamble_does_not_surface(self) -> None:
        blob = ''.join(self._frames())
        self.assertNotIn('permissions instructions', blob)

    def test_the_shapes_are_the_ones_the_chat_already_renders(self) -> None:
        kinds = []
        for frame in self._frames():
            payload = json.loads(frame.split('data: ', 1)[1].strip())
            kinds.append(payload['event']['raw']['type'])
        self.assertEqual(kinds, ['user', 'item.completed'])

    def test_a_chat_with_no_rollout_yields_nothing(self) -> None:
        self.record.agent_session_id = 'never-started'
        self.assertEqual(self._frames(), [])

    def test_a_record_with_no_id_yields_nothing(self) -> None:
        # A brand-new Codex chat that has not had its first turn yet.
        self.record.agent_session_id = ''
        self.assertEqual(self._frames(), [])

    def test_a_claude_record_never_reads_a_codex_rollout(self) -> None:
        self.record.agent_backend = 'claude'
        blob = ''.join(self._frames())
        self.assertNotIn('review my changes', blob)


class CodexChatIsListedTests(unittest.TestCase):
    """The restored chat must also appear in the Codex tab's chat list."""

    def test_the_records_backend_scopes_the_chat_list(self) -> None:
        from agent_core_lib.agent_core_lib.session.backend_chats import (
            parked_chat,
        )
        record = AgentSessionRecord(
            task_id='UNA-1', agent_backend='codex', agent_session_id=THREAD_ID,
        )
        self.assertEqual(
            parked_chat(record, 'codex')['agent_session_id'], THREAD_ID,
        )
        self.assertEqual(parked_chat(record, 'claude')['agent_session_id'], '')


if __name__ == '__main__':
    unittest.main()
