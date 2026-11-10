"""Deleting a task that is a live plan's source cascades: every sub-task it
created gets forgotten the same way (workspace, session, containers), its
open pull requests declined first. Against a real queue and real workspace
folders, not stubs — the point is that the folders are really gone after."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from plan_queue_core_lib.plan_queue_core_lib.data.plan import PlanMode, PlanStatus
from plan_queue_core_lib.plan_queue_core_lib.service import PlanQueueService
from plan_queue_core_lib.plan_queue_core_lib.store import PlanQueueStore
from plan_queue_core_lib.plan_queue_core_lib.tests.fakes import (
    FakeLauncher,
    FakeProbe,
    FakeProgress,
    FakeProposer,
    FakeWording,
    split_reply,
    step_spec,
    wait_until,
)
from workspace_core_lib.workspace_core_lib import WorkspaceCoreLib

from kato_webserver.app import create_app

SPLIT = split_reply([step_spec('api', repositories=['api']), step_spec('ui', depends_on=['api'])])


class ForgetTaskCascadeTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='forget-cascade-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name))
        env = patch.dict(os.environ, {
            'KATO_WORKSPACES_ROOT': str(self.root / 'ws'), 'AGENT_WORKSPACES_ROOT': str(self.root / 'ws'),
        })
        env.start()
        self.addCleanup(env.stop)
        self.workspaces = WorkspaceCoreLib(
            root=str(self.root / 'ws'), max_parallel_tasks=1,
            metadata_filename='.kato-meta.json', preflight_log_filename='.kato-preflight.log',
        ).workspaces
        self.workspaces.create(task_id='T-1', task_summary='Discounts', repository_ids=['api', 'web'])
        (self.root / 'ws' / 'T-1' / 'plan.md').write_text('# The plan\nDo it.\n', encoding='utf-8')
        self.queue = PlanQueueService(
            store=PlanQueueStore(self.root / 'plans'), proposer=FakeProposer(SPLIT),
            wording=FakeWording(), progress=FakeProgress(), probe=FakeProbe(),
        )
        self.queue.bind_launcher(FakeLauncher())
        self.addCleanup(self.queue.shutdown)
        self.repos = Mock()
        self.repos.task_branch_name.side_effect = lambda task_id: task_id
        self.repos.get_repository.side_effect = lambda repo_id: SimpleNamespace(id=repo_id)
        self.repos.decline_open_pull_requests.return_value = []
        from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
        manager = ClaudeSessionManager(state_dir=str(self.root / 'sessions'))
        self.addCleanup(manager.shutdown)
        self.app = create_app(
            session_manager=manager, workspace_manager=self.workspaces,
            agent_service=SimpleNamespace(plan_queue=self.queue, repository_service=self.repos),
        )
        self.client = self.app.test_client()

    def _approved_sub_task_ids(self) -> tuple[str, list[str]]:
        response = self.client.post('/api/sessions/T-1/plan-queue', json={})
        plan_id = response.get_json()['plan_id']
        self.assertTrue(wait_until(lambda: self.queue.get(plan_id).status == PlanStatus.PROPOSED))
        approved = self.client.post(f'/api/plan-queues/{plan_id}/approve', json={}).get_json()
        # ``creates_at_approval`` (FakeLauncher's default): every step gets a
        # task_id up front, whatever it depends on.
        task_ids = [step['task_id'] for step in approved['steps']]
        for task_id in task_ids:
            self.workspaces.create(task_id=task_id, task_summary='Sub-task', repository_ids=['api'])
        return plan_id, task_ids

    def test_cascade_deletes_every_sub_task_workspace_too(self) -> None:
        plan_id, task_ids = self._approved_sub_task_ids()
        self.assertEqual(len(task_ids), 2)

        answer = self.client.delete('/api/sessions/T-1/workspace')

        self.assertEqual(answer.status_code, 200, answer.get_json())
        body = answer.get_json()
        self.assertTrue(body['forgotten'])
        sub_tasks = {entry['task_id']: entry for entry in body['sub_tasks']}
        self.assertEqual(set(sub_tasks), set(task_ids))
        for task_id in task_ids:
            self.assertTrue(sub_tasks[task_id]['forgotten'], sub_tasks[task_id])
            self.assertFalse((self.root / 'ws' / task_id).exists())
        self.assertEqual(self.queue.get(plan_id).status, PlanStatus.CANCELLED)

    def test_a_followed_plan_is_left_alone(self) -> None:
        plan_id, task_ids = self._approved_sub_task_ids()
        self.queue._registry.mutate(plan_id, lambda plan: setattr(plan, 'mode', PlanMode.FOLLOWED))

        answer = self.client.delete('/api/sessions/T-1/workspace')

        self.assertEqual(answer.status_code, 200, answer.get_json())
        self.assertEqual(answer.get_json()['sub_tasks'], [])
        for task_id in task_ids:
            self.assertTrue((self.root / 'ws' / task_id).exists())  # untouched
        self.assertEqual(self.queue.get(plan_id).status, PlanStatus.APPROVED)  # untouched

    def test_a_declined_pull_request_failure_never_blocks_the_parent(self) -> None:
        plan_id, task_ids = self._approved_sub_task_ids()
        self.repos.decline_open_pull_requests.side_effect = RuntimeError('403 Forbidden')

        answer = self.client.delete('/api/sessions/T-1/workspace')

        self.assertEqual(answer.status_code, 200, answer.get_json())
        body = answer.get_json()
        self.assertTrue(body['forgotten'])  # the parent's own delete is unaffected
        for entry in body['sub_tasks']:
            self.assertIn('403 Forbidden', entry['decline_problems'][0])
            # Still forgotten: an un-declined PR just outlives the clone it
            # pointed at, same as any other direct task delete today — left
            # orphaned is worse than left undeclined.
            self.assertTrue(entry['forgotten'])
            self.assertFalse((self.root / 'ws' / entry['task_id']).exists())

    def test_a_step_never_prepared_has_nothing_to_cascade(self) -> None:
        # creates_at_approval=False: no step gets a task_id at approval, only
        # at release — nothing exists yet for the cascade to touch.
        self.queue.bind_launcher(FakeLauncher(creates_at_approval=False))
        response = self.client.post('/api/sessions/T-1/plan-queue', json={})
        plan_id = response.get_json()['plan_id']
        self.assertTrue(wait_until(lambda: self.queue.get(plan_id).status == PlanStatus.PROPOSED))
        self.client.post(f'/api/plan-queues/{plan_id}/approve', json={})

        answer = self.client.delete('/api/sessions/T-1/workspace')

        self.assertEqual(answer.status_code, 200, answer.get_json())
        self.assertEqual(answer.get_json()['sub_tasks'], [])

    def test_no_plan_queue_wired_cascades_nothing(self) -> None:
        self.app.config['AGENT_SERVICE'] = SimpleNamespace()
        self.workspaces.create(task_id='T-2', task_summary='No plan', repository_ids=['api'])

        answer = self.client.delete('/api/sessions/T-2/workspace')

        self.assertEqual(answer.status_code, 200, answer.get_json())
        self.assertEqual(answer.get_json()['sub_tasks'], [])

    def test_a_plan_with_no_steps_in_flight_is_not_stopped(self) -> None:
        plan_id, task_ids = self._approved_sub_task_ids()
        self.queue._registry.mutate(plan_id, lambda plan: setattr(plan, 'status', PlanStatus.CANCELLED))

        # stop() on an already-terminal plan raises (self-caught, logged) —
        # checking the final status alone can't tell "never called" apart
        # from "called, raised, caught", so spy on the call itself.
        with patch.object(self.queue, 'stop', wraps=self.queue.stop) as stop:
            answer = self.client.delete('/api/sessions/T-1/workspace')
            stop.assert_not_called()

        self.assertEqual(answer.status_code, 200, answer.get_json())
        self.assertEqual(self.queue.get(plan_id).status, PlanStatus.CANCELLED)
        for task_id in task_ids:
            self.assertFalse((self.root / 'ws' / task_id).exists())  # still cascaded

    def test_an_in_flight_plan_is_stopped_once_cascaded(self) -> None:
        plan_id, _task_ids = self._approved_sub_task_ids()

        with patch.object(self.queue, 'stop', wraps=self.queue.stop) as stop:
            self.client.delete('/api/sessions/T-1/workspace')
            stop.assert_called_once_with(plan_id)


if __name__ == '__main__':
    unittest.main()
