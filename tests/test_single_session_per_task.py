"""One Claude session per task — kato never starts a fresh one on its own.

The operator's rule: a task's conversation is ONE session. Kato must never
decide to abandon it and open a new one seeded with a summary of the old, and
must never let a live process that lost the history quietly become the task's
conversation. Context runs out? That is the operator's call to make — with
``/compact`` or an explicit new chat — not kato's, because a silent restart
throws away everything the agent learned and looks identical to it having
forgotten.

These tests pin the mechanism rather than the intention:

* the ONLY entry point that detaches a chat is ``start_new_chat``, which the
  webserver exposes on an explicit operator route;
* a live process reporting a different session id than the pinned record is
  terminated, and the PINNED id survives, so the next spawn resumes the real
  history instead of continuing the impostor;
* the same holds when ``--resume`` was silently ignored, which is the shape
  that produces a memoryless conversation wearing the right task's name.
"""

from __future__ import annotations

import ast
import pathlib
import types
import unittest
from unittest.mock import Mock

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MANAGER_PATH = (
    REPO_ROOT / 'claude_core_lib' / 'claude_core_lib' / 'session' / 'manager.py'
)


class NoAutomaticNewSessionTests(unittest.TestCase):
    """Nothing in the codebase may start a new chat without the operator."""

    def test_start_new_chat_is_only_called_from_the_operator_route(self) -> None:
        callers = []
        for path in REPO_ROOT.rglob('*.py'):
            parts = set(path.parts)
            if parts & {'node_modules', '.venv', 'build', 'dist', 'outputs'}:
                continue
            if 'tests' in parts or path.name.startswith('test_'):
                continue
            text = path.read_text(encoding='utf-8', errors='replace')
            if '.start_new_chat(' in text or 'def start_new_chat' in text:
                callers.append(str(path.relative_to(REPO_ROOT)))
        # The definition, the single webserver route that exposes it, and the
        # router that FORWARDS that route's call to the backend owning the
        # chat. The router decides nothing: it never originates a new chat,
        # it only picks which manager the operator's request reaches. The
        # invariant — no automatic new chats — is unchanged.
        self.assertEqual(
            sorted(callers),
            [
                'claude_core_lib/claude_core_lib/session/manager.py',
                'kato_core_lib/data_layers/service/agent_session_router.py',
                'webserver/kato_webserver/app.py',
            ],
            'a new caller of start_new_chat appeared — kato must never open a '
            'fresh chat except from the explicit operator route',
        )

    def test_no_scan_or_agent_service_reaches_for_a_new_chat(self) -> None:
        # The scan loop and the agent service are where an "it ran out of
        # context, just start over" shortcut would be tempting to add.
        for relative in (
            'kato_core_lib/jobs/process_assigned_tasks.py',
            'kato_core_lib/data_layers/service/agent_service.py',
            'kato_core_lib/data_layers/service/review_comment_service.py',
        ):
            path = REPO_ROOT / relative
            if not path.exists():
                continue
            with self.subTest(relative):
                self.assertNotIn(
                    'start_new_chat',
                    path.read_text(encoding='utf-8', errors='replace'),
                )

    def test_manager_exposes_exactly_one_chat_detaching_method(self) -> None:
        tree = ast.parse(MANAGER_PATH.read_text(encoding='utf-8'))
        names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and not node.name.startswith('_')
        }
        self.assertIn('start_new_chat', names)
        # No sibling that could grow into an automatic variant.
        self.assertEqual(
            {n for n in names if 'new_chat' in n or 'new_session' in n},
            {'start_new_chat'},
        )


class _FakeSession:
    """Minimal stand-in for a live streaming session."""

    def __init__(self, session_id: str, resume_was_ignored: bool = False) -> None:
        self.agent_session_id = session_id
        self.resume_was_ignored = resume_was_ignored
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True


class DriftedSessionIsDiscardedTests(unittest.TestCase):
    """A live process that isn't the pinned conversation gets dropped."""

    def _manager(self):
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        manager = ClaudeSessionManager.__new__(ClaudeSessionManager)
        manager.logger = Mock()
        manager._sessions = {}
        manager._records = {}
        return manager

    def test_mismatched_live_id_is_terminated_and_the_pin_survives(self) -> None:
        manager = self._manager()
        record = types.SimpleNamespace(
            task_id='PROJ-1', agent_session_id='pinned-id',
        )
        manager._records['PROJ-1'] = record
        session = _FakeSession('some-other-id')
        manager._sessions['PROJ-1'] = session

        dropped = manager._discard_if_session_id_drifted_locked(
            'PROJ-1', 'PROJ-1', session,
        )

        self.assertTrue(dropped)
        self.assertTrue(session.terminated)
        self.assertNotIn('PROJ-1', manager._sessions)
        # The whole point: the task keeps its real conversation.
        self.assertEqual(record.agent_session_id, 'pinned-id')

    def test_a_memoryless_resume_is_terminated_rather_than_adopted(self) -> None:
        # --resume silently ignored → the live process is a blank
        # conversation wearing the task's name. Adopting it would look
        # exactly like the agent forgetting everything.
        manager = self._manager()
        record = types.SimpleNamespace(
            task_id='PROJ-1', agent_session_id='pinned-id',
        )
        manager._records['PROJ-1'] = record
        session = _FakeSession('pinned-id', resume_was_ignored=True)
        manager._sessions['PROJ-1'] = session

        dropped = manager._discard_if_session_id_drifted_locked(
            'PROJ-1', 'PROJ-1', session,
        )

        self.assertTrue(dropped)
        self.assertTrue(session.terminated)
        self.assertEqual(record.agent_session_id, 'pinned-id')

    def test_matching_live_session_is_left_alone(self) -> None:
        manager = self._manager()
        record = types.SimpleNamespace(
            task_id='PROJ-1', agent_session_id='pinned-id',
        )
        manager._records['PROJ-1'] = record
        session = _FakeSession('pinned-id')
        manager._sessions['PROJ-1'] = session

        self.assertFalse(
            manager._discard_if_session_id_drifted_locked(
                'PROJ-1', 'PROJ-1', session,
            )
        )
        self.assertFalse(session.terminated)
        self.assertIn('PROJ-1', manager._sessions)


if __name__ == '__main__':
    unittest.main()
