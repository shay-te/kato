"""Per-backend chats on one task: switching must never lose a conversation.

The operator has a Claude thread open, asks Codex something, then comes back.
Both threads must be exactly where they left them. The record keeps the ACTIVE
chat in its top-level fields (so every existing reader is unchanged) and parks
the rest, which makes the swap the one place the two halves could disagree.
"""

from __future__ import annotations

import unittest

from agent_core_lib.agent_core_lib.session.backend_chats import (
    backends_with_chats,
    parked_chat,
    switch_backend,
)
from agent_core_lib.agent_core_lib.session.record import AgentSessionRecord


def _record(**kwargs) -> AgentSessionRecord:
    base = {
        'task_id': 'PROJ-1',
        'agent_backend': 'claude',
        'agent_session_id': 'claude-live',
        'previous_session_ids': ['claude-old'],
    }
    base.update(kwargs)
    return AgentSessionRecord(**base)


class SwitchTests(unittest.TestCase):
    def test_the_outgoing_chat_is_parked_not_dropped(self) -> None:
        record = switch_backend(_record(), 'codex')

        parked = record.chats_by_backend['claude']
        self.assertEqual(parked['agent_session_id'], 'claude-live')
        self.assertEqual(parked['previous_session_ids'], ['claude-old'])

    def test_the_incoming_backend_becomes_active_and_empty_when_new(self) -> None:
        record = switch_backend(_record(), 'codex')

        self.assertEqual(record.agent_backend, 'codex')
        self.assertEqual(record.agent_session_id, '')
        self.assertEqual(record.previous_session_ids, [])

    def test_switching_back_restores_the_original_conversation(self) -> None:
        # The whole point: an operator who tries the other agent and returns
        # must find their thread, not a blank one.
        record = switch_backend(_record(), 'codex')
        record.agent_session_id = 'codex-live'
        record.previous_session_ids = ['codex-old']

        record = switch_backend(record, 'claude')

        self.assertEqual(record.agent_backend, 'claude')
        self.assertEqual(record.agent_session_id, 'claude-live')
        self.assertEqual(record.previous_session_ids, ['claude-old'])
        self.assertEqual(
            record.chats_by_backend['codex']['agent_session_id'], 'codex-live',
        )

    def test_a_full_round_trip_leaves_both_chats_intact(self) -> None:
        record = _record()
        record = switch_backend(record, 'codex')
        record.agent_session_id = 'codex-live'
        record = switch_backend(record, 'claude')
        record = switch_backend(record, 'codex')

        self.assertEqual(record.agent_session_id, 'codex-live')
        self.assertEqual(
            record.chats_by_backend['claude']['agent_session_id'], 'claude-live',
        )

    def test_switching_to_the_active_backend_changes_nothing(self) -> None:
        # Re-parking on every tab render would rewrite the record constantly.
        record = switch_backend(_record(), 'claude')

        self.assertEqual(record.agent_session_id, 'claude-live')
        self.assertEqual(record.chats_by_backend, {})

    def test_casing_and_padding_do_not_create_a_second_entry(self) -> None:
        record = switch_backend(_record(), '  CODEX ')

        self.assertEqual(record.agent_backend, 'codex')
        self.assertEqual(sorted(record.chats_by_backend), ['claude'])

    def test_an_empty_backend_is_ignored(self) -> None:
        record = switch_backend(_record(), '')

        self.assertEqual(record.agent_backend, 'claude')
        self.assertEqual(record.agent_session_id, 'claude-live')

    def test_a_record_with_no_active_backend_parks_nothing(self) -> None:
        # Records written before backends were tracked. There is no name to
        # park the outgoing chat under, so it is left alone rather than
        # filed under a guess.
        record = _record(agent_backend='')

        result = switch_backend(record, 'codex')

        self.assertEqual(result.agent_backend, 'codex')
        self.assertEqual(result.chats_by_backend, {})

    def test_it_survives_a_disk_round_trip(self) -> None:
        record = switch_backend(_record(), 'codex')
        record.agent_session_id = 'codex-live'

        reloaded = AgentSessionRecord.from_dict(record.to_dict())

        self.assertEqual(reloaded.agent_backend, 'codex')
        self.assertEqual(reloaded.agent_session_id, 'codex-live')
        self.assertEqual(
            reloaded.chats_by_backend['claude']['agent_session_id'], 'claude-live',
        )


class ParkedChatTests(unittest.TestCase):
    def test_it_reads_the_ACTIVE_backend_from_the_top_level_fields(self) -> None:
        self.assertEqual(
            parked_chat(_record(), 'claude'),
            {'agent_session_id': 'claude-live',
             'previous_session_ids': ['claude-old']},
        )

    def test_it_reads_an_inactive_backend_from_the_map(self) -> None:
        record = switch_backend(_record(), 'codex')

        self.assertEqual(
            parked_chat(record, 'claude')['agent_session_id'], 'claude-live',
        )

    def test_an_empty_backend_means_this_records_own_chat(self) -> None:
        # Every record written before backends were tracked. Asking those for
        # a NAMED backend's chat would answer "none", and the operator's
        # existing conversation would vanish from the list.
        record = _record(agent_backend='')

        self.assertEqual(
            parked_chat(record, ''),
            {'agent_session_id': 'claude-live',
             'previous_session_ids': ['claude-old']},
        )

    def test_a_backend_with_no_chat_yet_is_empty_not_an_error(self) -> None:
        self.assertEqual(
            parked_chat(_record(), 'codex'),
            {'agent_session_id': '', 'previous_session_ids': []},
        )


class BackendsWithChatsTests(unittest.TestCase):
    def test_the_active_backend_comes_first(self) -> None:
        record = switch_backend(_record(), 'codex')

        self.assertEqual(backends_with_chats(record), ['codex', 'claude'])

    def test_a_task_with_one_chat_lists_only_it(self) -> None:
        self.assertEqual(backends_with_chats(_record()), ['claude'])

    def test_a_record_with_no_backend_at_all_lists_nothing(self) -> None:
        self.assertEqual(backends_with_chats(_record(agent_backend='')), [])


if __name__ == '__main__':
    unittest.main()
