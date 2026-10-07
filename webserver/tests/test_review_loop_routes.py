"""The review loop's HTTP surface and its hooks into the rest of the webserver.

Driven with a REAL ``ReviewLoopService`` (temp store) over a REAL git clone, so
the diff the reviewer gets is what ``_collect_review_diffs`` actually reads;
only the reviewer's agent run and the chat session are stand-ins.
"""
from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from review_loop_core_lib.review_loop_core_lib.service import ReviewLoopService
from review_loop_core_lib.review_loop_core_lib.store import ReviewLoopStore
from review_loop_core_lib.review_loop_core_lib.tests.fakes import (
    FAST,
    WORDING,
    FakeChat,
    FakeReviewer,
    finding,
    reply,
)

from kato_webserver import app as app_module
from kato_webserver.app import create_app


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ['git', *args], cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, 'init', '-q', '-b', 'main')
    _git(path, 'config', 'user.email', 't@example.com')
    _git(path, 'config', 'user.name', 'T')
    (path / 'app.py').write_text('def run():\n    return 1\n')
    _git(path, 'add', '.')
    _git(path, 'commit', '-q', '-m', 'base')
    return path


class _Workspace(object):
    def __init__(self, root: Path, repos: dict[str, Path]) -> None:
        self._root = root
        self._repos = repos

    def get(self, task_id):
        return SimpleNamespace(
            task_id=task_id, repository_ids=list(self._repos),
            task_summary='Add login', task_description='Email + password.',
        )

    def workspace_path(self, task_id):
        return self._root / task_id

    def repository_path(self, task_id, repo_id):
        return self._repos[repo_id]

    def list_workspaces(self):
        return [SimpleNamespace(
            task_id='T-1', repository_ids=list(self._repos), status='active',
            to_dict=lambda: {'task_id': 'T-1', 'status': 'active'},
        )]


class _RoutesBase(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = _repo(self.root / 'T-1' / 'api')
        (self.repo / 'app.py').write_text('def run():\n    return 2  # CHANGED\n')
        self.workspace = _Workspace(self.root, {'api': self.repo})
        self.chat = FakeChat()

    def client(self, reviewer, **service_kwargs):
        self.loops = ReviewLoopService(
            store=ReviewLoopStore(self.root / 'loops'), chat=self.chat,
            reviewer=reviewer, wording=WORDING, options=FAST, **service_kwargs,
        )
        self.addCleanup(self.loops.shutdown, 'test over')
        from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
        manager = ClaudeSessionManager(state_dir=str(self.root / 'sessions'))
        self.addCleanup(manager.shutdown)
        app = create_app(
            session_manager=manager, workspace_manager=self.workspace,
            agent_service=SimpleNamespace(review_loops=self.loops),
        )
        return app.test_client()

    def wait_finished(self) -> None:
        deadline = time.monotonic() + 5
        while self.loops.is_running('T-1') and time.monotonic() < deadline:
            time.sleep(0.01)


class ReviewLoopRouteTests(_RoutesBase):

    def test_no_loop_yet(self) -> None:
        body = self.client(FakeReviewer(reply())).get('/api/sessions/T-1/review-loop').get_json()
        self.assertEqual(body, {'loop': None})

    def test_start_runs_a_loop_over_the_real_diff(self) -> None:
        reviewer = FakeReviewer(reply(finding('MAJOR')), reply())
        client = self.client(reviewer)
        # The plain review/fix loop, every optional stage off.
        response = client.post('/api/sessions/T-1/review-loop', json=dict.fromkeys(
            ('self_check', 'verify_tests', 'confirm_clean', 'extra_sweep'), False,
        ))
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()['loop']['status'], 'running')
        self.wait_finished()

        loop = client.get('/api/sessions/T-1/review-loop').get_json()['loop']
        self.assertEqual(loop['status'], 'clean')
        self.assertEqual([r['outcome'] for r in loop['rounds']], ['sent', 'clean'])
        # The reviewer saw the working-tree change, and the ticket text.
        self.assertIn('return 2  # CHANGED', reviewer.prompts[0])
        self.assertIn('Email + password.', reviewer.prompts[0])
        self.assertEqual(len(self.chat.delivered), 1)

        artifact = client.get(
            f"/api/sessions/T-1/review-loop/{loop['loop_id']}/rounds/1/review",
        )
        self.assertEqual(artifact.status_code, 200)
        self.assertIn('review-verdict', artifact.get_json()['text'])
        for bad in (f"{loop['loop_id']}/rounds/9/review", f"{loop['loop_id']}/rounds/1/secrets",
                    'not-a-loop/rounds/1/review'):
            with self.subTest(path=bad):
                self.assertEqual(
                    client.get(f'/api/sessions/T-1/review-loop/{bad}').status_code, 404,
                )

    def test_every_stage_is_on_unless_the_request_turns_it_off(self) -> None:
        client = self.client(FakeReviewer(block_until_cancelled=True))
        loop = client.post('/api/sessions/T-1/review-loop').get_json()['loop']
        self.assertEqual(
            {k: loop[k] for k in ('self_check', 'verify_tests', 'confirm_clean', 'extra_sweep')},
            dict.fromkeys(('self_check', 'verify_tests', 'confirm_clean', 'extra_sweep'), True),
        )
        self.loops.stop('T-1')
        self.wait_finished()
        some_off = client.post('/api/sessions/T-1/review-loop', json={
            'self_check': False, 'extra_sweep': False, 'verify_tests': 'yes',
        }).get_json()['loop']
        # Only an explicit false turns a stage off.
        self.assertEqual(
            (some_off['self_check'], some_off['verify_tests'], some_off['confirm_clean'],
             some_off['extra_sweep']),
            (False, True, True, False),
        )

    def test_the_operator_picks_the_number_of_rounds(self) -> None:
        # A NEW blocking finding every review (a repeat would end it as stuck):
        # the loop runs to the picked limit.
        reviewer = FakeReviewer(reply(finding('MAJOR', symbol='first')),
                                reply(finding('MAJOR', symbol='second')),
                                reply(finding('MAJOR', symbol='third')))
        client = self.client(reviewer)
        response = client.post('/api/sessions/T-1/review-loop', json={'max_rounds': 2})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()['loop']['max_rounds'], 2)
        self.wait_finished()
        loop = client.get('/api/sessions/T-1/review-loop').get_json()['loop']
        self.assertEqual(loop['status'], 'max_rounds')
        self.assertEqual(len(loop['rounds']), 2)
        self.assertEqual(len(reviewer.prompts), 2)

    def test_no_pick_means_the_default(self) -> None:
        client = self.client(FakeReviewer(reply()))
        for body in (None, {}, {'max_rounds': None}):
            with self.subTest(body=body):
                response = client.post('/api/sessions/T-1/review-loop', json=body)
                self.assertEqual(response.status_code, 202)
                self.assertEqual(response.get_json()['loop']['max_rounds'], 5)
                self.wait_finished()

    def test_a_round_limit_outside_the_range_is_refused_not_clamped(self) -> None:
        client = self.client(FakeReviewer(reply()))
        for bad in (0, 31, -1, 2.5, '3', True):
            with self.subTest(max_rounds=bad):
                response = client.post('/api/sessions/T-1/review-loop', json={'max_rounds': bad})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()['error'],
                                 'max_rounds must be a whole number from 1 to 30')
        self.assertIsNone(self.loops.state('T-1'))
        for edge in (1, 30):
            with self.subTest(max_rounds=edge):
                response = client.post('/api/sessions/T-1/review-loop', json={'max_rounds': edge})
                self.assertEqual(response.get_json()['loop']['max_rounds'], edge)
                self.wait_finished()

    def test_a_refused_start_is_a_409_with_the_reason(self) -> None:
        client = self.client(
            FakeReviewer(reply()), can_start=lambda task_id: 'the chat is in Plan mode',
        )
        response = client.post('/api/sessions/T-1/review-loop')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['error'], 'the chat is in Plan mode')

    def test_stop(self) -> None:
        reviewer = FakeReviewer(block_until_cancelled=True)
        client = self.client(reviewer)
        self.assertEqual(client.post('/api/sessions/T-1/review-loop/stop').status_code, 404)
        client.post('/api/sessions/T-1/review-loop')
        self.assertTrue(reviewer.started.wait(5))
        response = client.post('/api/sessions/T-1/review-loop/stop')
        self.assertEqual(response.get_json(), {'stopped': True})
        self.wait_finished()
        loop = client.get('/api/sessions/T-1/review-loop').get_json()['loop']
        self.assertEqual(loop['status'], 'stopped')

    def test_the_tab_list_carries_where_the_loop_is(self) -> None:
        reviewer = FakeReviewer(block_until_cancelled=True)
        client = self.client(reviewer)
        client.post('/api/sessions/T-1/review-loop')
        self.assertTrue(reviewer.started.wait(5))
        tabs = client.get('/api/sessions').get_json()
        entry = next(tab for tab in tabs if tab['task_id'] == 'T-1')
        self.assertEqual(entry['review_loop']['phase'], 'reviewing')
        self.assertEqual(entry['review_loop']['round'], 1)
        self.assertEqual(entry['review_loop']['max_rounds'], 5)

    def test_without_a_configured_service_every_route_says_so(self) -> None:
        from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = create_app(session_manager=ClaudeSessionManager(state_dir=tmp.name), agent_service=None)
        client = app.test_client()
        for method, path in (('get', '/api/sessions/T-1/review-loop'),
                             ('post', '/api/sessions/T-1/review-loop'),
                             ('post', '/api/sessions/T-1/review-loop/stop'),
                             ('get', '/api/sessions/T-1/review-loop/' + 'a' * 32 + '/rounds/1/diff')):
            with self.subTest(path=path):
                self.assertEqual(getattr(client, method)(path).status_code, 503)


class HooksIntoTheRestTests(unittest.TestCase):
    """The chat's Stop and its chat/agent switches, against a real session
    manager running a real session (on the stand-in ``claude`` executable)
    and a real loop held running by a busy chat."""

    def setUp(self) -> None:
        from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
        from review_loop_core_lib.review_loop_core_lib.ports import ChatReadiness
        from tests.fake_claude_cli import FakeClaudeCli

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        cli = FakeClaudeCli(root / 'cli')
        self.sessions = ClaudeSessionManager(state_dir=str(root / 'sessions'))
        self.addCleanup(self.sessions.shutdown)
        self.sessions.start_session(task_id='T-1', binary=cli.binary, cwd=str(root))
        chat = FakeChat()
        chat.default_readiness = ChatReadiness.wait('the agent is mid-turn')
        self.loops = ReviewLoopService(
            store=ReviewLoopStore(root / 'loops'), chat=chat,
            reviewer=FakeReviewer(reply()), wording=WORDING, options=FAST,
        )
        self.addCleanup(self.loops.shutdown, 'test over')
        app = create_app(
            session_manager=self.sessions,
            agent_service=SimpleNamespace(review_loops=self.loops),
        )
        self.client = app.test_client()
        self.diffs = lambda task_id: []

    def _finished(self, task_id: str):
        deadline = time.monotonic() + 5
        while self.loops.is_running(task_id) and time.monotonic() < deadline:
            time.sleep(0.01)
        return self.loops.state(task_id)

    def test_the_chats_stop_also_stops_the_loop(self) -> None:
        self.loops.start('T-1', diff_source=self.diffs)
        self.assertEqual(self.client.post('/api/sessions/T-1/stop').status_code, 200)
        state = self._finished('T-1')
        self.assertEqual(state.status.value, 'stopped')
        self.assertEqual(state.reason, 'the chat was stopped')

    def test_the_loop_is_stopped_even_with_no_chat_session(self) -> None:
        self.loops.start('T-2', diff_source=self.diffs)
        self.assertEqual(self.client.post('/api/sessions/T-2/stop').status_code, 404)
        self.assertEqual(self._finished('T-2').status.value, 'stopped')

    def test_switching_chats_or_agents_mid_loop_is_refused(self) -> None:
        self.loops.start('T-1', diff_source=self.diffs)
        for path, body in (('/api/sessions/T-1/chats', {}),
                           ('/api/sessions/T-1/chats/handoff', {}),
                           ('/api/sessions/T-1/backend', {'agent_backend': 'codex'})):
            with self.subTest(path=path):
                response = self.client.post(path, json=body)
                self.assertEqual(response.status_code, 409)
                self.assertIn('review loop is running', response.get_json()['error'])
        self.assertTrue(self.loops.is_running('T-1'))

    def test_no_loop_means_no_refusal(self) -> None:
        response = self.client.post('/api/sessions/T-1/chats', json={})
        self.assertNotEqual(response.status_code, 409)


class CollectorTests(unittest.TestCase):

    def test_it_reads_every_repo_without_touching_git_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api = _repo(root / 'T-1' / 'api')
            web = _repo(root / 'T-1' / 'web')
            _git(web, 'checkout', '-q', '-b', 'somewhere-else')
            (api / 'app.py').write_text('def run():\n    return 3\n')
            app = SimpleNamespace(config={
                'WORKSPACE_MANAGER': _Workspace(root, {'api': api, 'web': web}),
                'AGENT_SERVICE': None,
            })
            diffs = app_module._collect_review_diffs(app, 'T-1')
            self.assertEqual([d.repo_id for d in diffs], ['api', 'web'])
            self.assertIn('+    return 3', diffs[0].diff)
            self.assertEqual(diffs[1].diff, '')
            # Read-only: the clone on another branch was NOT checked out.
            self.assertEqual(_git(web, 'rev-parse', '--abbrev-ref', 'HEAD'), 'somewhere-else')


if __name__ == '__main__':
    unittest.main()
