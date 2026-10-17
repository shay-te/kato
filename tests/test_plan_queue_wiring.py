"""The plan queue inside kato: built by AgentService, paused and started at boot."""

from __future__ import annotations

import inspect
import logging
import os
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from local_task_core_lib.local_task_core_lib.store import LocalTaskStore
from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan, PlanMode, PlanStatus
from plan_queue_core_lib.plan_queue_core_lib.service import PlanQueueService
from plan_queue_core_lib.plan_queue_core_lib.store import PlanQueueStore
from plan_queue_core_lib.plan_queue_core_lib.tests.fakes import (
    FakeProbe,
    FakeProgress,
    FakeProposer,
    FakeWording,
)
from workspace_core_lib.workspace_core_lib import WorkspaceCoreLib

from kato_core_lib import main as main_module
from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
from kato_core_lib.data_layers.service.agent_service import AgentService
from kato_core_lib.data_layers.service.local_task_routing import LocalAwareTaskService
from kato_core_lib.data_layers.service.plan_queue.parent_question import ParentQuestions
from kato_core_lib.data_layers.service.plan_queue.wiring import PLAN_QUEUES_DIR_ENV


class _Tracker(object):
    def get_assigned_tasks(self, project, assignee, states):
        return []


def _agent_service(test: unittest.TestCase, *, local: bool) -> AgentService:
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    root = Path(tmp.name)
    env = patch.dict(os.environ, {
        'KATO_WORKSPACES_ROOT': str(root / 'ws'), 'AGENT_WORKSPACES_ROOT': str(root / 'ws'),
        PLAN_QUEUES_DIR_ENV: str(root / 'plans'),
    })
    env.start()
    test.addCleanup(env.stop)
    workspaces = WorkspaceCoreLib(
        root=str(root / 'ws'), max_parallel_tasks=1,
        metadata_filename='.kato-meta.json', preflight_log_filename='.kato-preflight.log',
    ).workspaces
    task_service = object()
    if local:
        config = types.SimpleNamespace(
            project='P', assignee='me', issue_states=['Open'], progress_state='In Progress',
            review_state='In Review', done_state='Done', progress_state_field='State',
            review_state_field='State', done_state_field='State', open_state_field='State',
        )
        task_service = LocalAwareTaskService(
            config, TaskDataAccess(config, _Tracker()), LocalTaskStore(root / 'local.json'),
        )
    service = AgentService(
        task_service=task_service, task_state_service=object(), repository_service=object(),
        notification_service=object(),
        implementation_service=SimpleNamespace(stop_all_conversations=lambda: None),
        testing_service=SimpleNamespace(stop_all_conversations=lambda: None), workspace_manager=workspaces,
        wait_planning_service=SimpleNamespace(resolve_planning_context=lambda task: None) if local else None,
        logger=logging.getLogger('test.plan_queue.wiring'),
    )
    test.addCleanup(service.review_loops.shutdown, 'test over')
    return service


class AgentServiceWiringTests(unittest.TestCase):
    def test_built_when_local_tasks_are_wired(self) -> None:
        service = _agent_service(self, local=True)
        self.assertIsInstance(service.plan_queue, PlanQueueService)
        self.assertIs(service.plan_queue, service.plan_queue)
        # A sub-task's question goes to its plan's chat: asks THIS queue which plan.
        self.assertIsInstance(service.plan_questions, ParentQuestions)
        self.assertEqual(service.plan_questions.parent_of('T-404'), '')

    def test_absent_without_local_tasks(self) -> None:
        service = _agent_service(self, local=False)
        self.assertIsNone(service.plan_queue)
        self.assertIsNone(service.plan_questions)

    def test_shutdown_stops_the_queue_and_survives_its_failure(self) -> None:
        service = _agent_service(self, local=True)
        service.plan_queue.start()
        service.shutdown()
        self.assertFalse(service.plan_queue._thread.running)
        service._plan_questions = Mock()
        service.shutdown()
        service._plan_questions.shutdown.assert_called_once_with()
        service._plan_queue_service = Mock(shutdown=Mock(side_effect=RuntimeError('stuck')))
        with self.assertLogs('test.plan_queue.wiring', level='ERROR'):
            service.shutdown()


class BootHookTests(unittest.TestCase):
    def _queue(self) -> PlanQueueService:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = PlanQueueStore(tmp.name)
        running = Plan.new('T-1', plan_markdown='x', repository_ids=['api'], now=1.0)
        running.status = PlanStatus.RUNNING
        store.save(running)
        queue = PlanQueueService(store=store, proposer=FakeProposer(), wording=FakeWording(),
                                 progress=FakeProgress(), probe=FakeProbe())
        self.addCleanup(queue.shutdown)
        self.running_id = running.plan_id
        return queue

    def test_boot_pauses_a_running_plan(self) -> None:
        queue = self._queue()
        app = SimpleNamespace(service=SimpleNamespace(plan_queue=queue), logger=logging.getLogger('test.pq.boot'))
        main_module._mark_interrupted_plan_queues(app)
        self.assertEqual(queue.get(self.running_id).status, PlanStatus.PAUSED)

    def test_boot_steps_never_break_boot(self) -> None:
        for hook in (main_module._mark_interrupted_plan_queues, main_module._start_plan_queue):
            hook(SimpleNamespace(service=None, logger=logging.getLogger('test.pq.none')))
        broken = Mock(mark_interrupted=Mock(side_effect=OSError('unreadable')),
                      start=Mock(side_effect=RuntimeError('no thread')))
        app = SimpleNamespace(service=SimpleNamespace(plan_queue=broken), logger=logging.getLogger('test.pq.broken'))
        with self.assertLogs('test.pq.broken', level='ERROR'):
            main_module._mark_interrupted_plan_queues(app)
        with self.assertLogs('test.pq.broken', level='ERROR'):
            main_module._start_plan_queue(app)

    def test_start_binds_the_webserver_launcher_and_starts_ticking(self) -> None:
        queue = self._queue()
        flask_app = SimpleNamespace(config={})
        app = SimpleNamespace(service=SimpleNamespace(plan_queue=queue), planning_flask_app=flask_app,
                              logger=logging.getLogger('test.pq.start'))
        main_module._start_plan_queue(app)
        self.assertIs(queue._launchers[PlanMode.LOCAL]._app, flask_app)
        self.assertTrue(queue._thread.running)

    def test_without_a_webserver_nothing_is_launched_but_the_queue_ticks(self) -> None:
        queue = self._queue()
        app = SimpleNamespace(service=SimpleNamespace(plan_queue=queue), logger=logging.getLogger('test.pq.headless'))
        main_module._start_plan_queue(app)
        self.assertNotIn(PlanMode.LOCAL, queue._launchers)
        self.assertTrue(queue._thread.running)

    def test_both_hooks_are_part_of_boot(self) -> None:
        self.assertIn('_mark_interrupted_plan_queues(app)', inspect.getsource(main_module._run_boot_reconciliation))
        self.assertIn('_start_plan_queue(app)', inspect.getsource(main_module._start_post_boot_workers))


if __name__ == '__main__':
    unittest.main()
