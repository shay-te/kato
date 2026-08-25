"""Claude's transcript must never be replayed into another backend's tab.

Reported symptom: switching a task to the Codex tab showed the Claude
conversation. The cause was the session-id fallback chain — a task switched
to Codex correctly has an EMPTY active chat id, which fell through to the
workspace metadata mirror. That mirror is written by the Claude session and
is never cleared on a backend switch, so it happily handed back Claude's id
and the JSONL replay filled the Codex tab with Claude's messages.

An id is only meaningful next to the backend that issued it, so the resolver
now refuses to answer for a record whose active chat is not Claude's.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from claude_core_lib.claude_core_lib.session.history import (
    resolve_agent_session_id,
)


def _manager(record):
    manager = Mock()
    manager.get_record.return_value = record
    return manager


def _workspace_manager(session_id):
    manager = Mock()
    manager.get.return_value = SimpleNamespace(agent_session_id=session_id)
    return manager


class BackendGateTests(unittest.TestCase):
    """Only a Claude-active record resolves to a Claude transcript id."""

    def test_a_claude_record_resolves_its_own_id(self) -> None:
        record = SimpleNamespace(agent_backend='claude', agent_session_id='abc')
        self.assertEqual(
            resolve_agent_session_id(_manager(record), None, 'T1'), 'abc',
        )

    def test_a_legacy_record_with_no_backend_still_resolves(self) -> None:
        # Records predate backend tracking; treating them as non-Claude would
        # blank the scroll-back of every pre-existing chat.
        record = SimpleNamespace(agent_backend='', agent_session_id='abc')
        self.assertEqual(
            resolve_agent_session_id(_manager(record), None, 'T1'), 'abc',
        )

    def test_a_codex_record_resolves_to_nothing(self) -> None:
        record = SimpleNamespace(agent_backend='codex', agent_session_id='xyz')
        self.assertEqual(
            resolve_agent_session_id(_manager(record), None, 'T1'), '',
        )

    def test_a_codex_record_does_NOT_fall_back_to_the_workspace(self) -> None:
        """The reported bug, exactly.

        A freshly-switched Codex chat has an empty id. Falling through to the
        workspace mirror returned CLAUDE's id and replayed its transcript.
        """
        record = SimpleNamespace(agent_backend='codex', agent_session_id='')
        resolved = resolve_agent_session_id(
            _manager(record), _workspace_manager('claude-session-id'), 'T1',
        )
        self.assertEqual(resolved, '')

    def test_a_claude_record_with_no_id_still_uses_the_workspace(self) -> None:
        # The fallback exists so a freshly-booted webserver can attach to an
        # orphan workspace — that must keep working for Claude.
        record = SimpleNamespace(agent_backend='claude', agent_session_id='')
        resolved = resolve_agent_session_id(
            _manager(record), _workspace_manager('from-workspace'), 'T1',
        )
        self.assertEqual(resolved, 'from-workspace')

    def test_backend_matching_ignores_case_and_padding(self) -> None:
        record = SimpleNamespace(agent_backend='  CLAUDE ', agent_session_id='abc')
        self.assertEqual(
            resolve_agent_session_id(_manager(record), None, 'T1'), 'abc',
        )

    def test_no_record_still_uses_the_workspace(self) -> None:
        resolved = resolve_agent_session_id(
            _manager(None), _workspace_manager('orphan'), 'T1',
        )
        self.assertEqual(resolved, 'orphan')

    def test_a_manager_that_raises_falls_through(self) -> None:
        manager = Mock()
        manager.get_record.side_effect = RuntimeError('down')
        resolved = resolve_agent_session_id(
            manager, _workspace_manager('orphan'), 'T1',
        )
        self.assertEqual(resolved, 'orphan')

    def test_nothing_anywhere_resolves_to_empty(self) -> None:
        self.assertEqual(resolve_agent_session_id(None, None, 'T1'), '')


if __name__ == '__main__':
    unittest.main()
