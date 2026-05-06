"""Round-trip + serialization tests for :class:`WorkspaceRecord`."""

from __future__ import annotations

import unittest

from workspace_core_lib.workspace_core_lib.data_layers.data.workspace_record import (
    SUPPORTED_WORKSPACE_STATUSES,
    WORKSPACE_STATUS_ACTIVE,
    WORKSPACE_STATUS_DONE,
    WORKSPACE_STATUS_ERRORED,
    WORKSPACE_STATUS_PROVISIONING,
    WORKSPACE_STATUS_REVIEW,
    WORKSPACE_STATUS_TERMINATED,
    WorkspaceRecord,
)


class WorkspaceRecordTests(unittest.TestCase):
    def test_to_dict_round_trip_preserves_every_field(self) -> None:
        original = WorkspaceRecord(
            task_id='PROJ-9',
            task_summary='roundtrip test',
            status=WORKSPACE_STATUS_DONE,
            repository_ids=['repo1', 'repo2'],
            agent_session_id='sess-uuid',
            cwd='/tmp/work',
            resume_on_startup=False,
            created_at_epoch=100.0,
            updated_at_epoch=200.0,
        )
        round_trip = WorkspaceRecord.from_dict(original.to_dict())
        self.assertEqual(round_trip, original)

    def test_from_dict_accepts_legacy_claude_session_id_key(self) -> None:
        # Pre-rename deployments persisted the agent session id under
        # ``claude_session_id``. Read-side compat: we accept that key
        # so existing on-disk data loads without a migration script.
        legacy_payload = {
            'task_id': 'PROJ-1',
            'claude_session_id': 'legacy-sess-id',
        }
        record = WorkspaceRecord.from_dict(legacy_payload)
        self.assertEqual(record.agent_session_id, 'legacy-sess-id')

    def test_from_dict_prefers_new_key_when_both_present(self) -> None:
        payload = {
            'task_id': 'PROJ-1',
            'agent_session_id': 'new-id',
            'claude_session_id': 'legacy-id',
        }
        record = WorkspaceRecord.from_dict(payload)
        self.assertEqual(record.agent_session_id, 'new-id')

    def test_to_dict_uses_new_key_only(self) -> None:
        # Write-side: every persisted record uses the canonical name.
        # No ``claude_session_id`` gets written ever; legacy callers
        # are expected to migrate over time.
        record = WorkspaceRecord(
            task_id='PROJ-1', agent_session_id='abc',
        )
        payload = record.to_dict()
        self.assertIn('agent_session_id', payload)
        self.assertNotIn('claude_session_id', payload)

    def test_from_dict_tolerates_missing_optional_fields(self) -> None:
        # Hand-edited or partial payloads: only ``task_id`` is required.
        record = WorkspaceRecord.from_dict({'task_id': 'PROJ-1'})
        self.assertEqual(record.task_id, 'PROJ-1')
        self.assertEqual(record.task_summary, '')
        self.assertEqual(record.status, WORKSPACE_STATUS_PROVISIONING)
        self.assertEqual(record.repository_ids, [])
        self.assertEqual(record.agent_session_id, '')

    def test_from_dict_drops_non_string_repository_ids(self) -> None:
        record = WorkspaceRecord.from_dict({
            'task_id': 'PROJ-1',
            'repository_ids': ['ok', '', None, 'also-ok'],
        })
        self.assertEqual(record.repository_ids, ['ok', 'also-ok'])

    def test_from_dict_handles_invalid_repository_ids_field(self) -> None:
        # If the on-disk JSON has been corrupted to a non-list, treat
        # it as empty rather than raising — load must never crash.
        record = WorkspaceRecord.from_dict({
            'task_id': 'PROJ-1',
            'repository_ids': 'not-a-list',
        })
        self.assertEqual(record.repository_ids, [])

    def test_status_constants_are_in_supported_set(self) -> None:
        for status in (
            WORKSPACE_STATUS_PROVISIONING,
            WORKSPACE_STATUS_ACTIVE,
            WORKSPACE_STATUS_REVIEW,
            WORKSPACE_STATUS_DONE,
            WORKSPACE_STATUS_ERRORED,
            WORKSPACE_STATUS_TERMINATED,
        ):
            self.assertIn(status, SUPPORTED_WORKSPACE_STATUSES)


if __name__ == '__main__':
    unittest.main()
