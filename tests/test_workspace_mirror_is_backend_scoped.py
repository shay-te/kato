"""The workspace mirror must remember WHOSE session id it holds.

The mirror is a second copy of "which conversation belongs to this task",
kept next to the workspace folder so a wiped state dir can be recovered from
it at boot. It had exactly one ``agent_session_id`` field and no idea which
agent issued it.

That was harmless with one agent and wrong with two. Adoption for Codex is
routed through the record owner — the Claude manager — which mirrored
unconditionally, so a Codex thread id landed in the mirror looking exactly
like a Claude one. At the next boot the Claude seed folded it into the Claude
record, and the following spawn passed a Codex id to ``claude --resume``. It
finds no transcript, opens blank, and the operator's conversation looks lost.
"""
from __future__ import annotations

import tempfile
import threading
import unittest
from types import SimpleNamespace

from agent_core_lib.agent_core_lib.session.record import AgentSessionRecord
from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager


class _Workspaces(object):
    """Stand-in for the workspace manager, recording what it is told."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.updates = []

    def list_workspaces(self):
        return list(self.rows)

    def update_agent_session(self, task_id, **kwargs):
        self.updates.append({'task_id': task_id, **kwargs})


def _workspace(task_id='T1', session_id='', backend='', cwd='/w'):
    return SimpleNamespace(
        task_id=task_id,
        agent_session_id=session_id,
        agent_backend=backend,
        cwd=cwd,
        task_summary='',
    )


class _ManagerMixin(unittest.TestCase):
    def _manager(self, workspaces):
        with tempfile.TemporaryDirectory() as tmp:
            manager = ClaudeSessionManager(state_dir=tmp)
        manager._lock = threading.RLock()
        manager._records = {}
        manager._workspace_manager = workspaces
        manager._persist_record = lambda record: None
        return manager


class MirrorWritesTheBackendTests(_ManagerMixin):
    def test_it_stamps_the_record_s_own_backend(self) -> None:
        # THE ENTRY POINT. This manager also writes on behalf of another
        # backend (adoption for Codex is routed through the record owner),
        # so the stamp must follow the RECORD, not the manager.
        workspaces = _Workspaces()
        manager = self._manager(workspaces)
        manager._mirror_to_workspace_metadata(AgentSessionRecord(
            task_id='T1', task_summary='', agent_backend='codex',
            agent_session_id='codex-thread-9', cwd='/w',
        ))
        self.assertEqual(workspaces.updates[-1]['agent_backend'], 'codex')

    def test_its_own_records_are_stamped_claude(self) -> None:
        workspaces = _Workspaces()
        manager = self._manager(workspaces)
        manager._mirror_to_workspace_metadata(AgentSessionRecord(
            task_id='T1', task_summary='', agent_backend='claude',
            agent_session_id='claude-1', cwd='/w',
        ))
        self.assertEqual(workspaces.updates[-1]['agent_backend'], 'claude')

    def test_a_backendless_record_falls_back_to_this_manager(self) -> None:
        workspaces = _Workspaces()
        manager = self._manager(workspaces)
        manager._mirror_to_workspace_metadata(AgentSessionRecord(
            task_id='T1', task_summary='', agent_backend='',
            agent_session_id='legacy-1', cwd='/w',
        ))
        self.assertEqual(workspaces.updates[-1]['agent_backend'], 'claude')


class BootSeedRespectsTheBackendTests(_ManagerMixin):
    def test_another_backend_s_id_is_not_seeded(self) -> None:
        # THE DAMAGE. Folding this in would make the next spawn pass a Codex
        # id to ``claude --resume``.
        manager = self._manager(_Workspaces([
            _workspace(session_id='codex-thread-9', backend='codex'),
        ]))
        manager._seed_records_from_workspaces()
        self.assertEqual(manager._records, {})

    def test_its_own_id_is_still_recovered(self) -> None:
        # The guard must not break the feature it protects: recovering a
        # wiped state dir from the mirror is the whole point of the seed.
        manager = self._manager(_Workspaces([
            _workspace(session_id='claude-1', backend='claude'),
        ]))
        manager._seed_records_from_workspaces()
        self.assertEqual(
            manager._records[manager._lookup_key('T1')].agent_session_id, 'claude-1',
        )

    def test_a_legacy_mirror_with_no_backend_is_still_recovered(self) -> None:
        # Written before the stamp existed — and back then Claude was the
        # only backend, so it is ours.
        manager = self._manager(_Workspaces([
            _workspace(session_id='legacy-1', backend=''),
        ]))
        manager._seed_records_from_workspaces()
        self.assertEqual(
            manager._records[manager._lookup_key('T1')].agent_session_id, 'legacy-1',
        )


class TheWholeSequenceTests(_ManagerMixin):
    def test_adopting_for_codex_never_reaches_the_claude_record(self) -> None:
        """Adoption → mirror → restart → seed, end to end."""
        workspaces = _Workspaces()
        manager = self._manager(workspaces)

        # 1. The operator adopts a Codex thread. The record has moved to
        #    codex, and the mirror is written by THIS manager.
        manager._mirror_to_workspace_metadata(AgentSessionRecord(
            task_id='T1', task_summary='', agent_backend='codex',
            agent_session_id='codex-thread-9', cwd='/w',
        ))
        mirrored = workspaces.updates[-1]

        # 2. kato restarts with a wiped state dir; only the mirror survives.
        rebooted = self._manager(_Workspaces([
            _workspace(
                session_id=mirrored['agent_session_id'],
                backend=mirrored['agent_backend'],
            ),
        ]))
        rebooted._seed_records_from_workspaces()

        # The Claude record must NOT have been given the Codex id.
        self.assertEqual(rebooted._records, {})


if __name__ == '__main__':
    unittest.main()
