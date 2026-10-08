""""New task" end to end: the form's POST → a cloned, branched task → its first chat turn.

Real git repositories, the real RepositoryService / WorkspaceService /
WaitPlanningService clone-and-branch steps, the real webserver route, the real
PlanningSessionRunner and ClaudeSessionManager. Faked: the ``claude``
executable (``fake_claude_cli``, which records every message it is sent) and
the ticket platform — which must never be called at all.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
from kato_core_lib.data_layers.service.local_task_routing import (
    LocalAwareTaskService,
    LocalAwareTaskStateService,
)
from kato_core_lib.data_layers.service.local_task_service import LocalTaskService
from kato_core_lib.data_layers.service.repository_approval_service import (
    RepositoryApprovalService,
)
from kato_core_lib.data_layers.service.repository_service import RepositoryService
from kato_core_lib.data_layers.service.wait_planning_service import WaitPlanningService
from local_task_core_lib.local_task_core_lib.data.local_task import LocalTaskState
from local_task_core_lib.local_task_core_lib.store import LocalTaskStore
from tests.chaos_lib import build_real_workspace_service
from tests.fake_claude_cli import FakeClaudeCli, posix_only

TITLE = 'Add retry to the export job'
DESCRIPTION = 'The export job fails on timeouts. Retry 3 times with backoff.'


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ['git', *args], cwd=str(cwd), check=True, capture_output=True, text=True,
    ).stdout.strip()


class _NoTracker(object):
    """The ticket platform. A local task must never reach it."""

    def __getattr__(self, name):
        raise AssertionError(f'the tracker was called: {name}')


class _Repositories(RepositoryService):
    """The real service; only its logger is quiet and PR credentials skipped."""

    def __init__(self, source_root: Path) -> None:
        super().__init__(SimpleNamespace(repository_root_path=str(source_root)), 3)
        self._prepare_repository_access = lambda repository: repository
        quiet = lambda *args, **kwargs: None  # noqa: E731
        self.logger = SimpleNamespace(info=quiet, warning=quiet, exception=quiet,
                                      error=quiet, debug=quiet)


@unittest.skipUnless(posix_only(), 'the fake claude executable is a POSIX script')
class NewTaskEndToEndTests(unittest.TestCase):

    def setUp(self) -> None:
        from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner,
            StreamingSessionDefaults,
        )
        from kato_webserver import app as app_module
        from kato_webserver.app import create_app

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        # The operator's source checkout of "api", cloned from its remote.
        remote = root / 'remote' / 'api'
        remote.mkdir(parents=True)
        _git(remote, 'init', '-q', '-b', 'master')
        _git(remote, 'config', 'user.email', 't@example.com')
        _git(remote, 'config', 'user.name', 'test')
        (remote / 'export.py').write_text('def export():\n    return 1\n', encoding='utf-8')
        _git(remote, 'add', '-A')
        _git(remote, 'commit', '-qm', 'initial')
        self.source_root = root / 'dev'
        self.source_root.mkdir()
        _git(self.source_root, 'clone', '-q', str(remote), 'api')
        self.source = self.source_root / 'api'
        self.workspaces = root / 'workspaces'
        self.workspaces.mkdir()
        env = mock.patch.dict(os.environ, {
            'REPOSITORY_ROOT_PATH': str(self.source_root),
            'KATO_WORKSPACES_ROOT': str(self.workspaces),
        })
        env.start()
        self.addCleanup(env.stop)

        config = SimpleNamespace(project='P', assignee='me', issue_states=['Todo'])
        access = TaskDataAccess(config, _NoTracker())
        self.store = LocalTaskStore(root / 'local_tasks.json')
        task_service = LocalAwareTaskService(config, access, self.store)
        repositories = _Repositories(self.source_root)
        self.workspace = build_real_workspace_service(self.workspaces)
        planning = WaitPlanningService(
            session_manager=None, repository_service=repositories,
            task_state_service=LocalAwareTaskStateService(config, access, self.store),
            workspace_manager=self.workspace,
        )
        self.approvals = RepositoryApprovalService(root / 'approvals.json')
        self.approvals.approve('api', str(remote), approved_by='operator')
        service = LocalTaskService(
            task_service=task_service, repository_service=repositories,
            workspace_manager=self.workspace,
            prepare_workspace=planning.resolve_planning_context,
            approval_check=self.approvals.unapproved_repository_ids,
        )

        self.cli = FakeClaudeCli(root / 'cli')
        self.sessions = ClaudeSessionManager(state_dir=str(root / 'sessions'))
        self.addCleanup(self.sessions.shutdown)
        runner = PlanningSessionRunner(
            session_manager=self.sessions,
            defaults=StreamingSessionDefaults(binary=self.cli.binary),
        )
        # The levels the CLI's --help advertises — read from the real binary
        # in production; pinned here, the one external call left.
        levels = mock.patch.object(app_module, '_discover_chat_effort_levels',
                                   return_value=['low', 'medium', 'high'])
        levels.start()
        self.addCleanup(levels.stop)
        app = create_app(
            session_manager=self.sessions, workspace_manager=self.workspace,
            planning_session_runner=runner,
            agent_service=SimpleNamespace(local_tasks=service),
        )
        self.client = app.test_client()

    def create(self, **overrides):
        body = {'summary': TITLE, 'description': DESCRIPTION, 'repositories': ['api']}
        body.update(overrides)
        return self.client.post('/api/local-tasks', json=body)

    def first_message(self) -> dict:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            messages = self.cli.chat_messages()
            if messages:
                return messages[0]
            time.sleep(0.05)
        self.fail('the chat never received its first message')

    def wait_for_clone(self, task_id: str) -> Path:
        clone = self.workspaces / task_id / 'api'
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if (clone / '.git').exists() and _git(clone, 'rev-parse', '--abbrev-ref', 'HEAD') == task_id:
                return clone
            time.sleep(0.05)
        self.fail(f'{task_id} was never cloned onto its branch')

    def test_plan_is_the_default_and_the_agent_is_asked_for_a_plan(self) -> None:
        response = self.create(model='opus', effort='high')
        self.assertEqual(response.status_code, 202, response.get_json())
        body = response.get_json()
        self.assertEqual((body['task_id'], body['start_mode']), ('LOCAL-1', 'plan'))
        # The tab exists at once, before any clone: the tab list shows it.
        tabs = self.client.get('/api/sessions').get_json()
        self.assertIn('LOCAL-1', [tab['task_id'] for tab in tabs])

        message = self.first_message()
        argv = message['argv']
        self.assertEqual(argv[argv.index('--permission-mode') + 1], 'plan')
        self.assertEqual(argv[argv.index('--model') + 1], 'opus')
        self.assertEqual(argv[argv.index('--effort') + 1], 'high')
        self.assertIn(TITLE, message['text'])
        self.assertIn(DESCRIPTION, message['text'])
        self.assertIn('propose a step-by-step implementation plan', message['text'])

        clone = self.wait_for_clone('LOCAL-1')
        self.assertEqual(Path(message['cwd']).resolve(), clone.resolve())
        # The operator's own checkout was never branched.
        self.assertNotIn('LOCAL-1', _git(self.source, 'branch', '--format=%(refname:short)'))
        stored = self.store.get('LOCAL-1')
        self.assertEqual((stored.state, stored.tags), (LocalTaskState.IN_PROGRESS, ['kato:repo:api']))

    def test_implement_starts_editing_right_away(self) -> None:
        self.assertEqual(self.create(start_mode='implement').status_code, 202)
        message = self.first_message()
        argv = message['argv']
        self.assertNotEqual(argv[argv.index('--permission-mode') + 1], 'plan')
        self.assertIn('Implement this task.', message['text'])

    def test_just_open_the_chat_clones_and_sends_nothing(self) -> None:
        self.assertEqual(self.create(start_mode='chat').status_code, 202)
        self.wait_for_clone('LOCAL-1')
        time.sleep(0.3)
        self.assertEqual(self.cli.chat_messages(), [])

    def test_an_unapproved_repository_is_refused_and_nothing_is_left(self) -> None:
        self.approvals.revoke('api')
        response = self.create()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['unapproved_repositories'], ['api'])
        self.assertEqual(self.store.list(), [])
        self.assertFalse((self.workspaces / 'LOCAL-1').exists())

    def test_what_the_form_got_wrong_is_said_plainly(self) -> None:
        cases = {
            'give the task a title': {'summary': '  '},
            'pick at least one repository': {'repositories': []},
            'unknown repository': {'repositories': ['no-such-repo']},
            'unknown start mode': {'start_mode': 'yolo'},
            'unknown effort': {'effort': 'ludicrous'},
            'repositories must be a list': {'repositories': 'api'},
        }
        for expected, body in cases.items():
            response = self.create(**body)
            self.assertEqual(response.status_code, 400, expected)
            self.assertIn(expected, response.get_json()['error'])
        self.assertEqual(self.store.list(), [])


if __name__ == '__main__':
    unittest.main()
