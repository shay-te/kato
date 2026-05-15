"""Tests for review-state workspace protection during cleanup.

A ticket sitting in the review / "To Verify" bucket with a local clone
is work the operator may still be verifying, so its clone is *always*
protected from the stale sweep — regardless of age. (Previously a
review clone older than ``review_workspace_ttl_seconds`` was
force-cleaned; that wiped clones for tickets the operator was still
verifying — the "task disappeared while on verify" bug. The TTL now
only governs the active/provisioning grace window.)
"""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_core_lib.data_layers.service.workspace_manager import (
    WORKSPACE_STATUS_ACTIVE,
    WORKSPACE_STATUS_REVIEW,
)


def _import_agent_service():
    # Imported inside a helper so the test file doesn't pay the import cost
    # for unrelated agent_service collaborators when the suite runs.
    from kato_core_lib.data_layers.service.agent_service import AgentService
    return AgentService


def _build_agent_service(workspace_records, *, ttl: float, live_session_for=None):
    AgentService = _import_agent_service()
    workspace_manager = MagicMock()
    workspace_manager.list_workspaces.return_value = workspace_records
    session_manager = MagicMock()
    session_manager.list_records.return_value = []

    def _get_session(task_id):
        if live_session_for and task_id == live_session_for:
            return SimpleNamespace(is_alive=True)
        return None

    session_manager.get_session.side_effect = _get_session
    return AgentService(
        task_service=MagicMock(),
        task_state_service=MagicMock(),
        implementation_service=MagicMock(),
        testing_service=MagicMock(),
        repository_service=MagicMock(),
        notification_service=MagicMock(),
        workspace_manager=workspace_manager,
        session_manager=session_manager,
        review_workspace_ttl_seconds=ttl,
    )


def _ws(task_id: str, *, status: str, age_seconds: float) -> SimpleNamespace:
    return SimpleNamespace(
        task_id=task_id,
        status=status,
        updated_at_epoch=time.time() - age_seconds,
    )


class ReviewWorkspaceProtectionTests(unittest.TestCase):
    def test_review_workspace_within_ttl_is_kept(self) -> None:
        live_ids = {'UNA-1'}  # ticket still in review bucket
        ws = [_ws('UNA-1', status=WORKSPACE_STATUS_REVIEW, age_seconds=60)]
        service = _build_agent_service(ws, ttl=3600)
        stale = service._stale_planning_task_ids(live_ids)
        self.assertNotIn('UNA-1', stale, 'fresh review workspace must not be cleaned')

    def test_review_workspace_past_ttl_is_still_protected(self) -> None:
        """Regression for the UNA-232 "disappeared while on verify" bug.

        A review-state clone that has aged well past the TTL must NOT be
        cleaned. The operator may still be verifying it; deleting the
        clone made the task vanish from the UI mid-review.
        """
        live_ids = {'UNA-1'}  # still in review
        ws = [_ws('UNA-1', status=WORKSPACE_STATUS_REVIEW, age_seconds=7200)]
        service = _build_agent_service(ws, ttl=3600)
        stale = service._stale_planning_task_ids(live_ids)
        self.assertNotIn(
            'UNA-1', stale,
            'review workspace must be protected regardless of age',
        )

    def test_review_workspace_protected_even_when_ticket_not_in_bucket(self) -> None:
        """Clone protection is by workspace status, not by ticket fetch.

        Even if the platform fetch doesn't return the ticket this scan
        (transient API hiccup, id-case mismatch), a ``review`` clone on
        disk is still protected — the explicit status check shields it.
        """
        live_ids: set[str] = set()  # ticket absent from this scan
        ws = [_ws('UNA-232', status=WORKSPACE_STATUS_REVIEW, age_seconds=99999)]
        service = _build_agent_service(ws, ttl=3600)
        stale = service._stale_planning_task_ids(live_ids)
        self.assertNotIn(
            'UNA-232', stale,
            'a review clone on disk must never be swept',
        )

    def test_active_workspace_with_live_session_is_protected_past_ttl(self) -> None:
        # kato is mid-task on this one: a live session subprocess
        # proves it, so the cold timestamp does not make it stale.
        live_ids: set[str] = set()
        ws = [_ws('UNA-1', status=WORKSPACE_STATUS_ACTIVE, age_seconds=99999)]
        service = _build_agent_service(ws, ttl=3600, live_session_for='UNA-1')
        stale = service._stale_planning_task_ids(live_ids)
        self.assertNotIn('UNA-1', stale)

    def test_cold_active_workspace_without_session_is_stale(self) -> None:
        # No live session + cold (past grace) + ticket not live →
        # leftover of a finished task; must be cleaned (UNA-1201 fix).
        live_ids: set[str] = set()
        ws = [_ws('UNA-1', status=WORKSPACE_STATUS_ACTIVE, age_seconds=99999)]
        service = _build_agent_service(ws, ttl=3600)
        stale = service._stale_planning_task_ids(live_ids)
        self.assertIn('UNA-1', stale)

    def test_ttl_zero_keeps_review_workspace(self) -> None:
        live_ids = {'UNA-1'}
        ws = [_ws('UNA-1', status=WORKSPACE_STATUS_REVIEW, age_seconds=99999)]
        service = _build_agent_service(ws, ttl=0)
        stale = service._stale_planning_task_ids(live_ids)
        self.assertNotIn(
            'UNA-1', stale,
            'review workspace must be kept whatever the TTL setting',
        )


if __name__ == '__main__':
    unittest.main()
