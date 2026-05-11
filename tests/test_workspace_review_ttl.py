"""Tests for the review-state TTL on workspace cleanup.

Verify that a workspace in ``review`` whose ``updated_at_epoch`` is older
than the configured TTL becomes eligible for cleanup even if the ticket
is still in the review bucket.
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


def _build_agent_service(workspace_records, *, ttl: float):
    AgentService = _import_agent_service()
    workspace_manager = MagicMock()
    workspace_manager.list_workspaces.return_value = workspace_records
    return AgentService(
        task_service=MagicMock(),
        task_state_service=MagicMock(),
        implementation_service=MagicMock(),
        testing_service=MagicMock(),
        repository_service=MagicMock(),
        notification_service=MagicMock(),
        workspace_manager=workspace_manager,
        review_workspace_ttl_seconds=ttl,
    )


def _ws(task_id: str, *, status: str, age_seconds: float) -> SimpleNamespace:
    return SimpleNamespace(
        task_id=task_id,
        status=status,
        updated_at_epoch=time.time() - age_seconds,
    )


class ReviewTtlCleanupTests(unittest.TestCase):
    def test_review_workspace_within_ttl_is_kept(self) -> None:
        live_ids = {'UNA-1'}  # ticket still in review bucket
        ws = [_ws('UNA-1', status=WORKSPACE_STATUS_REVIEW, age_seconds=60)]
        service = _build_agent_service(ws, ttl=3600)
        stale = service._stale_planning_task_ids(live_ids)
        self.assertNotIn('UNA-1', stale, 'fresh review workspace must not be cleaned')

    def test_review_workspace_past_ttl_is_cleanable_even_if_live(self) -> None:
        live_ids = {'UNA-1'}  # still in review
        ws = [_ws('UNA-1', status=WORKSPACE_STATUS_REVIEW, age_seconds=7200)]
        service = _build_agent_service(ws, ttl=3600)
        stale = service._stale_planning_task_ids(live_ids)
        self.assertIn(
            'UNA-1', stale,
            'review workspace older than TTL must be eligible for cleanup',
        )

    def test_active_workspace_past_ttl_is_still_protected(self) -> None:
        # ACTIVE protection trumps TTL — kato is mid-task on this one.
        live_ids: set[str] = set()
        ws = [_ws('UNA-1', status=WORKSPACE_STATUS_ACTIVE, age_seconds=99999)]
        service = _build_agent_service(ws, ttl=3600)
        stale = service._stale_planning_task_ids(live_ids)
        self.assertNotIn('UNA-1', stale)

    def test_ttl_zero_disables_review_eviction(self) -> None:
        live_ids = {'UNA-1'}
        ws = [_ws('UNA-1', status=WORKSPACE_STATUS_REVIEW, age_seconds=99999)]
        service = _build_agent_service(ws, ttl=0)
        stale = service._stale_planning_task_ids(live_ids)
        self.assertNotIn(
            'UNA-1', stale,
            'TTL=0 must mean legacy behavior (no TTL-based eviction)',
        )


if __name__ == '__main__':
    unittest.main()
