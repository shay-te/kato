"""Targeted coverage tests for ``kato_webserver.app``.

These fill the remaining uncovered lines/branches in ``app.py`` that the
rest of the webserver suite leaves open: the model/effort discovery routes
(including the exception->fallback paths), the multi-repo Files/Changes/file
endpoints' skip-and-fallback branches, the SSE generator heartbeat/preflight
paths, the pre_tool_use hook rationale walk, the repository-approvals routes,
and a handful of small defensive branches.

Self-contained fakes only (no imports from sibling test modules) so this
file never clashes with parallel edits to the existing suites. No network,
no DB, no real subprocess — every external seam is a ``Mock`` / patch.
"""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent_core_lib.agent_core_lib.helpers.session_id_utils import AGENT_SESSION_ID
from kato_webserver import app as app_module
from kato_webserver.app import (
    _build_fallback_manager,
    _changed_files_for_repo,
    _chat_resume_context,
    _compute_repo_diff,
    _drain_queued_task_comment,
    _enumerate_repo_ids_from_disk,
    _event_stream_generator,
    _fire_webserver_hook,
    _follow_live_session,
    _live_session_ids,
    _replay_history_from_disk,
    _replay_preflight_log,
    _run_pre_tool_use_hook,
    _send_kato_png,
    _session_pending_permission_tool,
    _status_event_stream,
    create_app,
    main,
)


# --------------------------------------------------------------------------
# Minimal fakes
# --------------------------------------------------------------------------


class _Record:
    def __init__(self, **kwargs):
        self._payload = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)

    def to_dict(self):
        return dict(self._payload)


class _Manager:
    """Session manager stand-in keyed by task id."""

    def __init__(self, records=None, sessions=None):
        self._records = list(records or [])
        self._sessions = dict(sessions or {})

    def list_records(self):
        return list(self._records)

    def get_record(self, task_id):
        for record in self._records:
            if record.to_dict().get('task_id') == task_id:
                return record
        return None

    def get_session(self, task_id):
        return self._sessions.get(task_id)


class _WorkspaceManager:
    """Workspace manager stand-in with configurable repo/workspace paths."""

    def __init__(self, *, records=None, repo_paths=None, workspace_paths=None):
        self._records = dict(records or {})
        self._repo_paths = dict(repo_paths or {})
        self._workspace_paths = dict(workspace_paths or {})

    def get(self, task_id):
        return self._records.get(task_id)

    def repository_path(self, task_id, repo_id):
        return Path(self._repo_paths[(task_id, repo_id)])

    def workspace_path(self, task_id):
        return Path(self._workspace_paths.get(task_id, '/missing/path'))


# --------------------------------------------------------------------------
# _send_kato_png cache-control branch (693->695)
# --------------------------------------------------------------------------


class SendKatoPngTests(unittest.TestCase):
    def test_cache_control_header_is_applied_when_requested(self):
        app = create_app(session_manager=_Manager())
        with app.test_request_context():
            response = _send_kato_png(cache_control='public, max-age=99')
        # Real kato.png exists at the repo root, so this is a 200 file
        # response carrying the cache-control header we passed in.
        self.assertEqual(response.headers.get('Cache-Control'), 'public, max-age=99')
        response.close()


# --------------------------------------------------------------------------
# _enumerate_repo_ids_from_disk: non-dir entry skipped (180)
# --------------------------------------------------------------------------


class EnumerateRepoIdsTests(unittest.TestCase):
    def test_skips_non_directory_entries_and_dirs_without_git(self):
        with tempfile.TemporaryDirectory() as td:
            task_root = Path(td)
            # A plain file (not a dir) — must be skipped (line 180).
            (task_root / 'stray.txt').write_text('x', encoding='utf-8')
            # A dir without .git — skipped at the next guard.
            (task_root / 'no_git').mkdir()
            # A real repo dir with .git — included.
            repo = task_root / 'client'
            repo.mkdir()
            (repo / '.git').mkdir()
            manager = _WorkspaceManager(workspace_paths={'T-1': str(task_root)})
            self.assertEqual(
                _enumerate_repo_ids_from_disk(manager, 'T-1'), ['client'],
            )


# --------------------------------------------------------------------------
# _compute_repo_diff: task_id falsy -> skip ensure_branch_checked_out (553->555)
# --------------------------------------------------------------------------


class ComputeRepoDiffTests(unittest.TestCase):
    def test_empty_task_id_does_not_checkout_branch(self):
        with patch.object(app_module, 'ensure_branch_checked_out') as checkout, \
                patch.object(app_module, '_resolve_diff_base', return_value='main'), \
                patch.object(app_module, 'current_branch', return_value='task-br'), \
                patch.object(app_module, 'diff_against_base', return_value='DIFF'), \
                patch.object(app_module, 'conflicted_paths', return_value=[]):
            result = _compute_repo_diff('client', '/cwd', task_id='', agent_service=None)
        checkout.assert_not_called()
        self.assertEqual(result['diff'], 'DIFF')
        self.assertEqual(result['base'], 'main')

    def test_truthy_task_id_checks_out_branch(self):
        with patch.object(app_module, 'ensure_branch_checked_out') as checkout, \
                patch.object(app_module, '_resolve_diff_base', return_value='main'), \
                patch.object(app_module, 'current_branch', return_value='task-br'), \
                patch.object(app_module, 'diff_against_base', return_value='DIFF'), \
                patch.object(app_module, 'conflicted_paths', return_value=[]):
            _compute_repo_diff('client', '/cwd', task_id='T-1', agent_service=None)
        checkout.assert_called_once_with('/cwd', 'T-1')


# --------------------------------------------------------------------------
# /api/models + /api/effort-levels discovery (incl. fallbacks)
# --------------------------------------------------------------------------


class _Defaults:
    def __init__(self, *, binary='claude', effort=''):
        self.binary = binary
        self.effort = effort


def _runner_with_binary(binary):
    return SimpleNamespace(_defaults=_Defaults(binary=binary))


class ModelEffortDiscoveryTests(unittest.TestCase):
    def _client(self, runner=None):
        app = create_app(
            session_manager=_Manager(), planning_session_runner=runner,
        )
        return app.test_client()

    def test_models_route_uses_claude_discovery(self):
        client = self._client()
        with patch(
            'claude_core_lib.claude_core_lib.helpers.model_catalog.discover_models',
            return_value=[{'id': 'opus', 'label': 'Opus'}],
        ):
            body = client.get('/api/models').get_json()
        self.assertEqual(body['models'], [{'id': 'opus', 'label': 'Opus'}])

    def test_models_route_uses_codex_discovery_for_codex_binary(self):
        client = self._client(_runner_with_binary('/usr/bin/codex'))
        with patch(
            'codex_core_lib.codex_core_lib.helpers.model_discovery.discover_codex_models',
            return_value=[{'id': 'gpt', 'label': 'GPT'}],
        ) as codex_discover:
            body = client.get('/api/models').get_json()
        codex_discover.assert_called_once()
        self.assertEqual(body['models'], [{'id': 'gpt', 'label': 'GPT'}])

    def test_models_route_falls_back_on_discovery_exception(self):
        client = self._client()
        with patch(
            'claude_core_lib.claude_core_lib.helpers.model_catalog.discover_models',
            side_effect=RuntimeError('no network'),
        ):
            body = client.get('/api/models').get_json()
        # Fallback set is non-empty and shaped like dicts.
        self.assertTrue(body['models'])
        self.assertTrue(all(isinstance(m, dict) for m in body['models']))

    def test_effort_levels_route_uses_discovery(self):
        client = self._client()
        with patch(
            'claude_core_lib.claude_core_lib.helpers.effort_levels.discover_effort_levels',
            return_value=['low', 'high'],
        ):
            body = client.get('/api/effort-levels').get_json()
        self.assertEqual(body['levels'], ['low', 'high'])

    def test_effort_levels_route_falls_back_on_exception(self):
        client = self._client()
        with patch(
            'claude_core_lib.claude_core_lib.helpers.effort_levels.discover_effort_levels',
            side_effect=RuntimeError('cannot run --help'),
        ):
            body = client.get('/api/effort-levels').get_json()
        # FALLBACK_EFFORT_LEVELS — non-empty list of level strings.
        self.assertTrue(body['levels'])
        self.assertIn('medium', body['levels'])

    def test_effort_default_reflects_configured_runner_effort(self):
        runner = SimpleNamespace(_defaults=_Defaults(binary='claude', effort='high'))
        client = self._client(runner)
        with patch(
            'claude_core_lib.claude_core_lib.helpers.effort_levels.discover_effort_levels',
            return_value=['low', 'high'],
        ):
            body = client.get('/api/effort-levels').get_json()
        self.assertEqual(body['default'], 'high')


# --------------------------------------------------------------------------
# /api/sessions/<id>/effort: not-wired 503 before validation (851)
# --------------------------------------------------------------------------


class EffortOverrideRouteTests(unittest.TestCase):
    def test_set_effort_returns_503_when_store_is_none(self):
        app = create_app(session_manager=_Manager())
        app.config['TASK_EFFORT_OVERRIDES'] = None
        response = app.test_client().post(
            '/api/sessions/T-1/effort', json={'effort': 'high'},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {'error': 'not available'})


# --------------------------------------------------------------------------
# /api/sessions/<id>: idle record -> empty recent_events (886->887)
# --------------------------------------------------------------------------


class GetSessionTests(unittest.TestCase):
    def test_idle_record_returns_empty_recent_events(self):
        manager = _Manager(records=[_Record(task_id='T-1', task_summary='x')])
        app = create_app(session_manager=manager)
        body = app.test_client().get('/api/sessions/T-1').get_json()
        self.assertFalse(body['live'])
        self.assertEqual(body['recent_events'], [])


# --------------------------------------------------------------------------
# /api/claude/sessions: adopted-by map (915-919)
# --------------------------------------------------------------------------


class ListClaudeSessionsTests(unittest.TestCase):
    def test_marks_sessions_already_adopted_by_a_task(self):
        # A record whose session id matches a disk session -> the row is
        # tagged with adopted_by_task_id.
        manager = _Manager(records=[
            _Record(task_id='T-1', agent_session_id='sid-1'),
        ])
        app = create_app(session_manager=manager)

        row = SimpleNamespace(
            agent_session_id='sid-1',
            to_dict=lambda: {'agent_session_id': 'sid-1', 'cwd': '/w'},
        )
        with patch.object(app_module, 'read_session_id_from', return_value='sid-1'), \
                patch(
                    'claude_core_lib.claude_core_lib.session.index.list_sessions',
                    return_value=[row],
                ):
            body = app.test_client().get('/api/claude/sessions').get_json()

        self.assertEqual(len(body['sessions']), 1)
        entry = body['sessions'][0]
        self.assertEqual(entry['adopted_by_task_id'], 'T-1')
        self.assertEqual(entry[AGENT_SESSION_ID], 'sid-1')
        # The duplicate agent_session_id key was stripped from the spread.
        self.assertNotIn('agent_session_id', {
            k: v for k, v in entry.items() if k != AGENT_SESSION_ID
        })


# --------------------------------------------------------------------------
# awaiting-push-approval: not-callable check (1765)
# --------------------------------------------------------------------------


class AwaitingPushApprovalTests(unittest.TestCase):
    def test_returns_false_when_check_not_callable(self):
        # agent_service present, but is_awaiting_push_approval is not a method.
        agent_service = SimpleNamespace(is_awaiting_push_approval='not-callable')
        app = create_app(session_manager=_Manager(), agent_service=agent_service)
        body = app.test_client().get(
            '/api/sessions/T-1/awaiting-push-approval',
        ).get_json()
        self.assertFalse(body['awaiting_push_approval'])
        self.assertEqual(body['task_id'], 'T-1')


# --------------------------------------------------------------------------
# Files / Changes / file endpoints: multi-repo skip + fallback branches
# --------------------------------------------------------------------------


class FilesAndDiffMultiRepoTests(unittest.TestCase):
    def _multi_repo_app(self, tmp):
        """Two repos in metadata; only ``client`` exists on disk."""
        client_dir = Path(tmp) / 'client'
        client_dir.mkdir()
        (client_dir / '.git').mkdir()
        workspace = _WorkspaceManager(
            records={'T-1': SimpleNamespace(repository_ids=['client', 'ghost'])},
            repo_paths={
                ('T-1', 'client'): str(client_dir),
                # ``ghost`` resolves to a non-existent path -> cwd is None,
                # exercising the ``continue`` skip branches.
                ('T-1', 'ghost'): str(Path(tmp) / 'ghost-missing'),
            },
            workspace_paths={'T-1': tmp},
        )
        return create_app(
            session_manager=_Manager(), workspace_manager=workspace,
        ), client_dir

    def test_files_route_skips_missing_repo_and_returns_present_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _client_dir = self._multi_repo_app(tmp)
            with patch.object(app_module, 'tracked_file_tree', return_value=[{'n': 1}]), \
                    patch.object(app_module, 'conflicted_paths', return_value=[]), \
                    patch.object(app_module, '_changed_files_for_repo', return_value=[]):
                body = app.test_client().get('/api/sessions/T-1/files').get_json()
        # Only ``client`` survived (1394 continue skipped ``ghost``); the
        # non-empty trees branch (1410->1419) returned the legacy mirror.
        self.assertEqual(body['repository_ids'], ['client'])
        self.assertEqual(body['cwd'], str(_under(body)))
        self.assertEqual(len(body['trees']), 1)

    def test_diff_route_skips_missing_repo_and_returns_present_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _client_dir = self._multi_repo_app(tmp)
            fake_diff = {
                'repo_id': 'client', 'cwd': '/c', 'base': 'main',
                'head': 'task', 'diff': 'D', 'conflicted_files': [], 'error': '',
            }
            with patch.object(app_module, '_compute_repo_diff', return_value=fake_diff), \
                    patch.object(app_module, '_workspace_status', return_value='active'):
                body = app.test_client().get('/api/sessions/T-1/diff').get_json()
        # 1465 continue skipped ``ghost``; 1469->1481 returned the first diff.
        self.assertEqual(body['repository_ids'], ['client'])
        self.assertEqual(body['repo_id'], 'client')
        self.assertEqual(body['diff'], 'D')
        self.assertEqual(body['workspace_status'], 'active')


def _under(body):
    return body['trees'][0]['cwd']


class FileRouteTests(unittest.TestCase):
    def _single_repo_app(self, tmp):
        repo = Path(tmp) / 'client'
        repo.mkdir()
        (repo / '.git').mkdir()
        workspace = _WorkspaceManager(
            records={'T-1': SimpleNamespace(repository_ids=['client'])},
            repo_paths={('T-1', 'client'): str(repo)},
            workspace_paths={'T-1': tmp},
        )
        app = create_app(
            session_manager=_Manager(), workspace_manager=workspace,
        )
        return app, repo

    def test_relative_path_happy_read(self):
        # The normal relative-path read: resolves against the repo root
        # and returns the file's content (covers the success join branch).
        with tempfile.TemporaryDirectory() as tmp:
            app, repo = self._single_repo_app(tmp)
            (repo / 'file.txt').write_text('hello', encoding='utf-8')
            body = app.test_client().get(
                '/api/sessions/T-1/file', query_string={'path': 'file.txt'},
            ).get_json()
            self.assertEqual(body['content'], 'hello')
            self.assertFalse(body['binary'])

    def test_absolute_path_resolve_oserror_returns_400(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _repo = self._single_repo_app(tmp)
            real_resolve = Path.resolve

            def flaky(self, *a, **k):
                if str(self) == '/abs/bad':
                    raise OSError('nope')
                return real_resolve(self, *a, **k)

            with patch.object(Path, 'resolve', flaky):
                response = app.test_client().get(
                    '/api/sessions/T-1/file', query_string={'path': '/abs/bad'},
                )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()['error'], 'invalid path')

    def test_relative_root_join_oserror_is_skipped(self):
        # The candidate-join loop (1561-1565) swallows an OSError from
        # one root and continues; with no valid candidate the route 403s.
        with tempfile.TemporaryDirectory() as tmp:
            app, repo = self._single_repo_app(tmp)
            real_resolve = Path.resolve

            def flaky(self, *a, **k):
                # Any resolve of a path under the repo raises -> both the
                # candidate loop (1564) and resolved-roots loop (1570) skip.
                if str(repo) in str(self):
                    raise OSError('boom')
                return real_resolve(self, *a, **k)

            with patch.object(Path, 'resolve', flaky):
                response = app.test_client().get(
                    '/api/sessions/T-1/file', query_string={'path': 'thing.txt'},
                )
            # No candidate landed inside any (unresolvable) root -> 403.
            self.assertEqual(response.status_code, 403)

    def test_path_inside_root_but_missing_returns_404(self):
        # in_workspace set but file absent -> resolved stays None ->
        # 'file not found' 404 (1593-1594).
        with tempfile.TemporaryDirectory() as tmp:
            app, _repo = self._single_repo_app(tmp)
            response = app.test_client().get(
                '/api/sessions/T-1/file', query_string={'path': 'absent.txt'},
            )
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.get_json()['error'], 'file not found')

    def test_stat_oserror_returns_500(self):
        # 1599-1600: the explicit ``resolved.stat()`` size read raises. The
        # route stats f.txt three times first (candidate is_file, guard
        # is_file, then the size read), so we let those through and fail the
        # fourth call — the size read — emulating a vanished file.
        with tempfile.TemporaryDirectory() as tmp:
            app, repo = self._single_repo_app(tmp)
            target = repo / 'f.txt'
            target.write_text('hi', encoding='utf-8')
            real_stat = Path.stat
            seen = {'n': 0}

            def counting_stat(self, *a, **k):
                if self.name == 'f.txt':
                    seen['n'] += 1
                    if seen['n'] >= 4:
                        raise OSError('stat boom')
                return real_stat(self, *a, **k)

            with patch.object(Path, 'stat', counting_stat):
                response = app.test_client().get(
                    '/api/sessions/T-1/file', query_string={'path': 'f.txt'},
                )
            self.assertEqual(response.status_code, 500)
            self.assertIn('stat failed', response.get_json()['error'])

    def test_non_utf8_content_is_replaced(self):
        # 1622-1623: a file with invalid UTF-8 decodes with errors=replace.
        with tempfile.TemporaryDirectory() as tmp:
            app, repo = self._single_repo_app(tmp)
            target = repo / 'bin.txt'
            # Invalid UTF-8 (0xff) but no NUL byte so it isn't "binary".
            target.write_bytes(b'abc\xffdef')
            response = app.test_client().get(
                '/api/sessions/T-1/file', query_string={'path': 'bin.txt'},
            )
            body = response.get_json()
            self.assertFalse(body['binary'])
            self.assertIn('�', body['content'])

    def test_legacy_root_fallback_when_no_workspace_repos(self):
        # 1537->: no workspace repo ids -> falls back to the record cwd
        # root, and reads a file from it.
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / 'legacy'
            legacy.mkdir()
            (legacy / 'a.txt').write_text('legacy', encoding='utf-8')
            manager = _Manager(records=[_Record(task_id='T-1', cwd=str(legacy))])
            # No workspace manager -> _task_repository_ids returns [] ->
            # roots come from the record cwd.
            app = create_app(session_manager=manager)
            body = app.test_client().get(
                '/api/sessions/T-1/file', query_string={'path': 'a.txt'},
            ).get_json()
            self.assertEqual(body['content'], 'legacy')


# --------------------------------------------------------------------------
# forget-task: no session manager -> skip terminate branch (2128->2140);
# workspace_path raises after delete (2155-2156).
# --------------------------------------------------------------------------


class ForgetWorkspaceBranchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {
            'KATO_FORGOTTEN_TASKS_PATH': str(Path(self._tmp.name) / 'forgotten.json'),
        })
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_no_session_manager_skips_terminate_and_succeeds(self):
        workspace = MagicMock()
        workspace.delete.return_value = None
        # workspace_path points at a non-existent dir -> exists() False.
        workspace.workspace_path.return_value = Path(self._tmp.name) / 'gone'
        app = create_app(
            session_manager=_Manager(), workspace_manager=workspace,
        )
        # Force the SESSION_MANAGER config slot to None so the terminate
        # branch (2128->2140) is skipped entirely.
        app.config['SESSION_MANAGER'] = None
        response = app.test_client().delete('/api/sessions/T-1/workspace')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['forgotten'])
        workspace.delete.assert_called_once_with('T-1')

    def test_workspace_path_raises_after_delete_still_succeeds(self):
        workspace = MagicMock()
        workspace.delete.return_value = None
        workspace.workspace_path.side_effect = RuntimeError('no path')
        app = create_app(
            session_manager=_Manager(), workspace_manager=workspace,
        )
        app.config['SESSION_MANAGER'] = None
        response = app.test_client().delete('/api/sessions/T-1/workspace')
        # workspace_dir resolves to None -> no "still exists" error.
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['forgotten'])


# --------------------------------------------------------------------------
# Status SSE stream (2196, 2238-2241)
# --------------------------------------------------------------------------


class StatusStreamTests(unittest.TestCase):
    def test_status_stream_emits_backlog_then_new_entries_then_ping(self):
        entry0 = SimpleNamespace(sequence=1, to_dict=lambda: {'sequence': 1})
        entry1 = SimpleNamespace(sequence=2, to_dict=lambda: {'sequence': 2})

        broadcaster = MagicMock()
        broadcaster.recent.return_value = [entry0]
        # First wait yields a new entry (covers 2236-2238); second wait
        # returns nothing AND the heartbeat window has elapsed -> ': ping'
        # (2239-2241); third wait raises to terminate the generator.
        broadcaster.wait_for_new.side_effect = [
            [entry1],
            [],
            RuntimeError('stop'),
        ]

        # Call #1: baseline last_heartbeat. Iteration 1 has new entries so
        # monotonic isn't read again. Iteration 2 has no entries: call #2
        # (100.0) - baseline (0.0) >= 15 -> ': ping' fires, then call #3
        # resets last_heartbeat. Iteration 3 raises to end the generator.
        clock = iter([0.0, 100.0, 100.0])

        def fake_monotonic():
            try:
                return next(clock)
            except StopIteration:
                return 999.0

        frames = []
        with patch.object(app_module.time, 'monotonic', fake_monotonic):
            gen = _status_event_stream(broadcaster)
            try:
                for frame in gen:
                    frames.append(frame)
                    if len(frames) > 10:  # pragma: no cover - safety bound
                        break
            except (StopIteration, RuntimeError):
                pass

        joined = ''.join(frames)
        self.assertIn(': open', joined)
        self.assertIn('"sequence": 1', joined)
        self.assertIn('"sequence": 2', joined)
        self.assertIn(': ping', joined)

    def test_status_events_route_streams_when_broadcaster_present(self):
        entry = SimpleNamespace(sequence=1, to_dict=lambda: {'sequence': 1})
        broadcaster = MagicMock()
        broadcaster.recent.return_value = [entry]
        broadcaster.wait_for_new.side_effect = RuntimeError('stop')
        app = create_app(
            session_manager=_Manager(), status_broadcaster=broadcaster,
        )
        # Route construction (2196) wraps the generator; pulling one frame
        # confirms the wiring without looping forever.
        response = app.test_client().get('/api/status/events')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers['Content-Type'], 'text/event-stream; charset=utf-8',
        )
        try:
            first = next(response.response)
        except Exception:
            first = b''
        self.assertIn(b': open', first)


# --------------------------------------------------------------------------
# _replay_preflight_log: real entries yielded (2785-2801)
# --------------------------------------------------------------------------


class ReplayPreflightLogTests(unittest.TestCase):
    def test_yields_preflight_history_frames_for_each_entry(self):
        workspace = SimpleNamespace(
            read_preflight_log=lambda task_id: [
                (1.0, 'cloning 1/2: client'),
                (2.0, 'done'),
            ],
        )
        frames = list(_replay_preflight_log(workspace, 'T-1'))
        self.assertEqual(len(frames), 2)
        joined = ''.join(frames)
        self.assertIn('preflight', joined)
        self.assertIn('cloning 1/2: client', joined)


# --------------------------------------------------------------------------
# _follow_live_session: heartbeat path (2883-2887)
# --------------------------------------------------------------------------


class FollowLiveSessionHeartbeatTests(unittest.TestCase):
    def test_heartbeat_ping_emitted_while_alive(self):
        session = MagicMock()
        # is_alive: True for two loop iterations, then False to close.
        session.is_alive = True
        alive_states = iter([True, True, False])

        type(session).is_alive = property(
            lambda self: next(alive_states, False),
        )
        # No new events on each poll.
        session.events_after.return_value = ([], 0)

        # First monotonic() is the baseline last_heartbeat; subsequent
        # readings jump past the heartbeat window so a ': ping' fires.
        clock = iter([0.0, 100.0, 100.0, 200.0, 200.0, 300.0])

        def fake_monotonic():
            return next(clock, 1000.0)

        with patch.object(app_module.time, 'monotonic', fake_monotonic), \
                patch.object(app_module.time, 'sleep') as sleep:
            frames = list(_follow_live_session(session))

        sleep.assert_called()  # the poll-interval sleep ran at least once
        joined = ''.join(frames)
        self.assertIn(': ping', joined)
        self.assertIn('session_closed', joined)


# --------------------------------------------------------------------------
# _run_pre_tool_use_hook: rationale walk branches (2468-2470, 2478-2484)
# --------------------------------------------------------------------------


class PreToolUseHookTests(unittest.TestCase):
    def _app(self, runner):
        return create_app(session_manager=_Manager(), hook_runner=runner)

    def test_fire_exception_returns_not_blocked(self):
        runner = MagicMock()
        runner.fire.side_effect = RuntimeError('hook crash')
        app = self._app(runner)
        with app.test_request_context():
            blocked, rationale = _run_pre_tool_use_hook(
                app, 'T-1', {'request_id': 'r', 'tool': 'Bash', 'allow': True},
            )
        self.assertFalse(blocked)
        self.assertEqual(rationale, '')

    def test_no_results_returns_not_blocked(self):
        runner = MagicMock()
        runner.fire.return_value = []
        app = self._app(runner)
        with app.test_request_context():
            blocked, rationale = _run_pre_tool_use_hook(app, 'T-1', {})
        self.assertFalse(blocked)
        self.assertEqual(rationale, '')

    def test_blocked_walk_skips_unblocked_and_empty_rationale(self):
        # results[0]: not blocked -> 2479->2478 skip.
        # results[1]: blocked but empty stderr/error -> 2481->2478 continue.
        # results[2]: blocked with stderr -> rationale set, break.
        runner = MagicMock()
        results = [
            SimpleNamespace(blocked=False, stderr='ignored', error=''),
            SimpleNamespace(blocked=True, stderr='', error=''),
            SimpleNamespace(blocked=True, stderr='final reason', error=''),
        ]
        runner.fire.return_value = results
        runner.is_blocked.return_value = True
        app = self._app(runner)
        with app.test_request_context():
            blocked, rationale = _run_pre_tool_use_hook(app, 'T-1', {})
        self.assertTrue(blocked)
        self.assertEqual(rationale, 'final reason')

    def test_blocked_walk_exhausts_without_rationale(self):
        # Every blocked result has empty stderr/error -> loop runs to the
        # end without a break (2478->2483); rationale stays ''.
        runner = MagicMock()
        results = [
            SimpleNamespace(blocked=True, stderr='', error=''),
            SimpleNamespace(blocked=True, stderr='', error=''),
        ]
        runner.fire.return_value = results
        runner.is_blocked.return_value = True
        app = self._app(runner)
        with app.test_request_context():
            blocked, rationale = _run_pre_tool_use_hook(app, 'T-1', {})
        self.assertTrue(blocked)
        self.assertEqual(rationale, '')


# --------------------------------------------------------------------------
# post_message: effort-change respawn path (2347-2355)
# --------------------------------------------------------------------------


class PostMessageEffortRespawnTests(unittest.TestCase):
    def test_effort_change_terminates_and_respawns(self):
        # A live idle session at a different effort than the override
        # must be terminated and respawned via the runner.
        session = SimpleNamespace(
            is_alive=True, is_working=False, effort='low',
        )
        manager = MagicMock()
        manager.get_session.return_value = session
        runner = MagicMock()
        app = create_app(
            session_manager=manager, planning_session_runner=runner,
        )
        app.config['WORKSPACE_MANAGER'] = None
        app.config['TASK_EFFORT_OVERRIDES'] = {'T-1': 'high'}

        with patch.object(
            app_module, '_chat_resume_context', return_value=('/cwd', 'sum'),
        ), patch.object(
            app_module, '_chat_additional_dirs', return_value=[],
        ):
            response = app.test_client().post(
                '/api/sessions/T-1/messages', json={'text': 'go'},
            )

        manager.terminate_session.assert_called_once_with('T-1', remove_record=False)
        runner.resume_session_for_chat.assert_called_once()
        self.assertEqual(response.get_json()['status'], 'spawned')

    def test_effort_change_terminate_failure_is_swallowed(self):
        # 2350-2354: terminate raises -> logged, then still respawns.
        session = SimpleNamespace(
            is_alive=True, is_working=False, effort='low',
        )
        manager = MagicMock()
        manager.get_session.return_value = session
        manager.terminate_session.side_effect = RuntimeError('term boom')
        runner = MagicMock()
        app = create_app(
            session_manager=manager, planning_session_runner=runner,
        )
        app.config['WORKSPACE_MANAGER'] = None
        app.config['TASK_EFFORT_OVERRIDES'] = {'T-1': 'high'}

        with patch.object(
            app_module, '_chat_resume_context', return_value=('/cwd', 'sum'),
        ), patch.object(
            app_module, '_chat_additional_dirs', return_value=[],
        ):
            response = app.test_client().post(
                '/api/sessions/T-1/messages', json={'text': 'go'},
            )

        self.assertEqual(response.get_json()['status'], 'spawned')


# --------------------------------------------------------------------------
# _migrate_adopted_session_transcript: matched + unmatched (2605-2610)
# --------------------------------------------------------------------------


class MigrateTranscriptTests(unittest.TestCase):
    def test_migrates_when_session_id_matches(self):
        from kato_webserver.app import _migrate_adopted_session_transcript

        app = create_app(session_manager=_Manager())
        entry = SimpleNamespace(
            agent_session_id='sid-1', transcript_path='/path/sid-1.jsonl',
        )
        with patch.object(
            app_module, '_chat_resume_context', return_value=('/cwd', 's'),
        ), patch(
            'claude_core_lib.claude_core_lib.session.index.list_sessions',
            return_value=[entry],
        ), patch(
            'claude_core_lib.claude_core_lib.session.index.migrate_session_to_workspace',
            return_value='/cwd/projects/x/sid-1.jsonl',
        ) as migrate:
            result = _migrate_adopted_session_transcript(app, 'T-1', 'sid-1')
        self.assertEqual(result, '/cwd/projects/x/sid-1.jsonl')
        migrate.assert_called_once()

    def test_returns_none_when_no_matching_transcript(self):
        from kato_webserver.app import _migrate_adopted_session_transcript

        app = create_app(session_manager=_Manager())
        entry = SimpleNamespace(
            agent_session_id='other', transcript_path='/path/other.jsonl',
        )
        with patch.object(
            app_module, '_chat_resume_context', return_value=('/cwd', 's'),
        ), patch(
            'claude_core_lib.claude_core_lib.session.index.list_sessions',
            return_value=[entry],
        ):
            result = _migrate_adopted_session_transcript(app, 'T-1', 'sid-1')
        self.assertIsNone(result)


# --------------------------------------------------------------------------
# _chat_resume_context: workspace repo path fallback (2641-2647)
# --------------------------------------------------------------------------


class ChatResumeContextTests(unittest.TestCase):
    def test_falls_back_to_first_repo_path_when_cwd_blank(self):
        from kato_webserver.app import _chat_resume_context

        workspace = MagicMock()
        workspace.get.return_value = SimpleNamespace(
            cwd='', task_summary='S', repository_ids=['client'],
        )
        workspace.repository_path.return_value = '/ws/T-1/client'
        cwd, summary = _chat_resume_context(None, workspace, 'T-1')
        self.assertEqual(cwd, '/ws/T-1/client')
        self.assertEqual(summary, 'S')

    def test_repo_path_lookup_exception_leaves_cwd_blank(self):
        from kato_webserver.app import _chat_resume_context

        workspace = MagicMock()
        workspace.get.return_value = SimpleNamespace(
            cwd='', task_summary='', repository_ids=['client'],
        )
        workspace.repository_path.side_effect = RuntimeError('no path')
        cwd, _summary = _chat_resume_context(None, workspace, 'T-1')
        self.assertEqual(cwd, '')


# --------------------------------------------------------------------------
# _chat_additional_dirs: empty repo path skip (2683-2684)
# --------------------------------------------------------------------------


class ChatAdditionalDirsTests(unittest.TestCase):
    def test_skips_empty_repo_path(self):
        from kato_webserver.app import _chat_additional_dirs

        workspace = MagicMock()
        workspace.get.return_value = SimpleNamespace(repository_ids=['a', 'b'])
        # 'a' -> empty string (skipped at 2683-2684); 'b' -> real path.
        workspace.repository_path.side_effect = lambda t, r: '' if r == 'a' else '/ws/b'
        result = _chat_additional_dirs(workspace, 'T-1', cwd='/other')
        self.assertEqual(result, ['/ws/b'])


# --------------------------------------------------------------------------
# _session_pending_permission_tool: legacy history walk (3168->3156, 3170)
# --------------------------------------------------------------------------


class PendingPermissionToolTests(unittest.TestCase):
    def test_history_walk_passes_neutral_events_then_returns_empty(self):
        from kato_webserver.app import _session_pending_permission_tool

        # No live-probe -> legacy history walk. A neutral 'assistant' event
        # neither matches a request nor a response, so the loop continues
        # past it (3168->3156) and, finding nothing pending, returns ''
        # (3170).
        session = SimpleNamespace(
            recent_events=lambda: [
                SimpleNamespace(raw={'type': 'assistant'}),
                SimpleNamespace(raw={'type': 'user'}),
            ],
        )
        self.assertEqual(_session_pending_permission_tool(session), '')

    def test_history_walk_returns_empty_on_response_event(self):
        from kato_webserver.app import _session_pending_permission_tool

        # A permission_response newest -> '' (3168-3169 return path).
        from claude_core_lib.claude_core_lib.session.wire_protocol import (
            CLAUDE_EVENT_PERMISSION_RESPONSE,
        )
        session = SimpleNamespace(
            recent_events=lambda: [
                SimpleNamespace(raw={'type': CLAUDE_EVENT_PERMISSION_RESPONSE}),
            ],
        )
        self.assertEqual(_session_pending_permission_tool(session), '')


# --------------------------------------------------------------------------
# _workspace_record_to_dict: agent-session backfill + awaiting check
# (3205-3214)
# --------------------------------------------------------------------------


class WorkspaceRecordToDictTests(unittest.TestCase):
    def test_backfills_agent_session_id_and_awaiting_push(self):
        from kato_webserver.app import _workspace_record_to_dict

        record = SimpleNamespace(
            task_id='T-1',
            to_dict=lambda: {'task_id': 'T-1', AGENT_SESSION_ID: ''},
        )
        awaiting = MagicMock(return_value=True)
        payload = _workspace_record_to_dict(
            record,
            live_session_ids=set(),
            session_ids_by_task={'T-1': 'sid-backfill'},
            awaiting_push_check=awaiting,
        )
        # 3206-3208: empty agent_session_id backfilled from the map.
        self.assertEqual(payload[AGENT_SESSION_ID], 'sid-backfill')
        # 3210-3215: awaiting check honoured.
        self.assertTrue(payload['has_changes_pending'])

    def test_awaiting_push_check_exception_is_swallowed(self):
        from kato_webserver.app import _workspace_record_to_dict

        record = SimpleNamespace(
            task_id='T-1', to_dict=lambda: {'task_id': 'T-1'},
        )

        def boom(_):
            raise RuntimeError('check failed')

        payload = _workspace_record_to_dict(
            record, live_session_ids=set(), awaiting_push_check=boom,
        )
        self.assertFalse(payload['has_changes_pending'])


# --------------------------------------------------------------------------
# _record_to_dict: dict + bare-object branches (3222-3224)
# --------------------------------------------------------------------------


class RecordToDictTests(unittest.TestCase):
    def test_plain_dict_is_copied(self):
        from kato_webserver.app import _record_to_dict

        src = {'task_id': 'T-1', 'x': 1}
        out = _record_to_dict(src)
        self.assertEqual(out, src)
        self.assertIsNot(out, src)

    def test_bare_object_yields_task_id_only(self):
        from kato_webserver.app import _record_to_dict

        out = _record_to_dict(SimpleNamespace(task_id='T-9'))
        self.assertEqual(out, {'task_id': 'T-9'})


# --------------------------------------------------------------------------
# _build_fallback_manager (745, 3249-3254) + create_app() with no manager
# --------------------------------------------------------------------------


class FallbackManagerTests(unittest.TestCase):
    def test_create_app_without_manager_builds_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            app = create_app(fallback_state_dir=td)
            self.assertIsNotNone(app.config['SESSION_MANAGER'])

    def test_build_fallback_manager_returns_claude_manager(self):
        with tempfile.TemporaryDirectory() as td:
            manager = _build_fallback_manager(td)
            # ClaudeSessionManager exposes list_records.
            self.assertTrue(hasattr(manager, 'list_records'))


# --------------------------------------------------------------------------
# Repository approvals routes (1239-1291, 1310-1354)
# --------------------------------------------------------------------------


class RepositoryApprovalsRoutesTests(unittest.TestCase):
    def _client(self):
        return create_app(session_manager=_Manager()).test_client()

    def test_get_repository_approvals_lists_candidates_and_orphans(self):
        candidate = SimpleNamespace(
            repository_id='client', remote_url='https://x/client.git',
            source='inventory', workspace_path='/ws/client',
        )
        approved_entry = SimpleNamespace(
            repository_id='client',
            remote_url='https://x/client.git',
            approval_mode=SimpleNamespace(value='restricted'),
            approved_by='op',
        )
        orphan_entry = SimpleNamespace(
            repository_id='gone',
            remote_url='https://x/gone.git',
            approval_mode=SimpleNamespace(value='trusted'),
            approved_by='op2',
        )
        service = MagicMock()
        service.list_approvals.return_value = [approved_entry, orphan_entry]
        service.storage_path = '/cfg/approvals.json'

        with patch(
            'kato_core_lib.data_layers.service.repository_approval_discovery_service.discover_all_repositories',
            return_value=[candidate],
        ), patch(
            'kato_core_lib.data_layers.service.repository_approval_service.RepositoryApprovalService',
            return_value=service,
        ):
            body = self._client().get('/api/repository-approvals').get_json()

        ids = {row['repository_id'] for row in body['repositories']}
        self.assertEqual(ids, {'client', 'gone'})
        client_row = next(r for r in body['repositories'] if r['repository_id'] == 'client')
        self.assertTrue(client_row['approved'])
        gone_row = next(r for r in body['repositories'] if r['repository_id'] == 'gone')
        self.assertEqual(gone_row['source'], 'orphan')
        self.assertEqual(body['storage_path'], '/cfg/approvals.json')

    def test_post_repository_approvals_applies_approve_and_revoke(self):
        from kato_core_lib.data_layers.data.repository_approval import ApprovalMode

        service = MagicMock()
        service.approve.return_value = SimpleNamespace(
            repository_id='client',
            approval_mode=SimpleNamespace(value='trusted'),
        )
        service.revoke.return_value = True

        with patch(
            'kato_core_lib.data_layers.service.repository_approval_service.RepositoryApprovalService',
            return_value=service,
        ):
            body = self._client().post('/api/repository-approvals', json={
                'approve': [
                    {'repository_id': 'client', 'remote_url': 'u', 'mode': 'trusted'},
                    'not-a-dict',
                    {'repository_id': '', 'remote_url': 'u'},
                    {'repository_id': 'weird', 'mode': 'bogus-mode'},
                ],
                'revoke': ['old-repo', '', None],
            }).get_json()

        self.assertTrue(body['ok'])
        self.assertEqual(
            [a['repository_id'] for a in body['applied']['approved']],
            ['client', 'client'],  # 'weird' falls back to RESTRICTED, still applied
        )
        self.assertEqual(body['applied']['revoked'], ['old-repo'])
        # The bogus mode resolved to RESTRICTED via the except branch.
        modes_called = [c.kwargs.get('mode') for c in service.approve.call_args_list]
        self.assertIn(ApprovalMode.RESTRICTED, modes_called)

    def test_post_repository_approvals_rejects_non_array_payload(self):
        with patch(
            'kato_core_lib.data_layers.service.repository_approval_service.RepositoryApprovalService',
        ):
            response = self._client().post(
                '/api/repository-approvals', json={'approve': 'oops'},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn('arrays', response.get_json()['error'])


# --------------------------------------------------------------------------
# _send_kato_png: no cache-control header branch (693->695)
# --------------------------------------------------------------------------


class SendKatoPngNoCacheTests(unittest.TestCase):
    def test_no_custom_cache_control_keeps_send_file_default(self):
        # 693->695: empty cache_control skips the header override, so the
        # response carries send_file's own default rather than ours.
        app = create_app(session_manager=_Manager())
        with app.test_request_context():
            response = _send_kato_png(cache_control='')
        # send_file defaults to 'no-cache'; our custom value is NOT applied.
        self.assertEqual(response.headers.get('Cache-Control'), 'no-cache')
        response.close()


# --------------------------------------------------------------------------
# asset_url: missing file falls back to plain url (776-777)
# --------------------------------------------------------------------------


class AssetUrlTests(unittest.TestCase):
    def test_missing_asset_returns_unversioned_url(self):
        # The context processor's asset_url falls back to the bare URL
        # (no ``?v=``) when the file is absent (stat raises OSError).
        app = create_app(session_manager=_Manager())
        with app.test_request_context():
            # Run the registered context processors to obtain asset_url.
            context = {}
            for processor in app.template_context_processors[None]:
                context.update(processor())
            url = context['asset_url']('does/not/exist.js')
        self.assertNotIn('?v=', url)
        self.assertIn('does/not/exist.js', url)

    def test_present_asset_is_version_busted(self):
        # The True path: an existing static file gets a ``?v=<mtime>``.
        app = create_app(session_manager=_Manager())
        with app.test_request_context():
            context = {}
            for processor in app.template_context_processors[None]:
                context.update(processor())
            url = context['asset_url']('build/app.js')
        self.assertIn('?v=', url)


# --------------------------------------------------------------------------
# _changed_files_for_repo: base resolves -> changed_paths used (607)
# --------------------------------------------------------------------------


class ChangedFilesForRepoTests(unittest.TestCase):
    def test_returns_changed_paths_when_base_resolves(self):
        with patch.object(app_module, '_resolve_diff_base', return_value='main'), \
                patch.object(
                    app_module, 'changed_paths', return_value=['a.py', 'b.py'],
                ) as changed:
            result = _changed_files_for_repo('client', '/cwd', agent_service=None)
        self.assertEqual(result, ['a.py', 'b.py'])
        changed.assert_called_once_with('/cwd', 'origin/main')

    def test_returns_empty_when_no_base(self):
        with patch.object(app_module, '_resolve_diff_base', return_value=''):
            self.assertEqual(
                _changed_files_for_repo('client', '/cwd', agent_service=None), [],
            )


# --------------------------------------------------------------------------
# /api/claude/sessions: blank session id skipped (918->916)
# --------------------------------------------------------------------------


class ClaudeSessionsBlankIdTests(unittest.TestCase):
    def test_blank_session_id_does_not_populate_adopted_map(self):
        manager = _Manager(records=[_Record(task_id='T-1', agent_session_id='')])
        app = create_app(session_manager=manager)
        row = SimpleNamespace(
            agent_session_id='sid-x',
            to_dict=lambda: {'agent_session_id': 'sid-x', 'cwd': '/w'},
        )
        with patch.object(app_module, 'read_session_id_from', return_value=''), \
                patch(
                    'claude_core_lib.claude_core_lib.session.index.list_sessions',
                    return_value=[row],
                ):
            body = app.test_client().get('/api/claude/sessions').get_json()
        # No record contributed a session id -> the row is unadopted.
        self.assertEqual(body['sessions'][0]['adopted_by_task_id'], '')


# --------------------------------------------------------------------------
# Files / Diff: multi-repo with ALL repos missing -> legacy fallback
# (1410->1419, 1469->1481, 1537->1535 trees-empty fall-through)
# --------------------------------------------------------------------------


class MultiRepoAllMissingFallbackTests(unittest.TestCase):
    def _app_all_missing(self, tmp, legacy_cwd):
        # Metadata lists a repo that does NOT exist on disk, so every
        # _repository_cwd is None and the multi-repo loops produce no
        # trees/diffs, falling through to the legacy record-cwd path.
        workspace = _WorkspaceManager(
            records={'T-1': SimpleNamespace(repository_ids=['ghost'])},
            repo_paths={('T-1', 'ghost'): str(Path(tmp) / 'nope')},
            workspace_paths={'T-1': tmp},
        )
        manager = _Manager(records=[_Record(task_id='T-1', cwd=legacy_cwd)])
        return create_app(session_manager=manager, workspace_manager=workspace)

    def test_files_falls_back_to_legacy_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / 'legacy'
            legacy.mkdir()
            app = self._app_all_missing(tmp, str(legacy))
            with patch.object(app_module, 'tracked_file_tree', return_value=[{'x': 1}]), \
                    patch.object(app_module, 'conflicted_paths', return_value=[]), \
                    patch.object(app_module, '_changed_files_for_repo', return_value=[]):
                body = app.test_client().get('/api/sessions/T-1/files').get_json()
        # Legacy single-tree shape (repository_ids empty).
        self.assertEqual(body['repository_ids'], [])
        self.assertEqual(body['cwd'], str(legacy))
        self.assertEqual(body['tree'], [{'x': 1}])

    def test_diff_falls_back_to_legacy_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / 'legacy'
            legacy.mkdir()
            app = self._app_all_missing(tmp, str(legacy))
            fake = {
                'repo_id': '', 'cwd': str(legacy), 'base': 'main',
                'head': 'task', 'diff': 'D', 'conflicted_files': [], 'error': '',
            }
            with patch.object(app_module, '_compute_repo_diff', return_value=fake), \
                    patch.object(app_module, '_workspace_status', return_value=''):
                body = app.test_client().get('/api/sessions/T-1/diff').get_json()
        self.assertEqual(body['repository_ids'], [])
        self.assertEqual(body['diff'], 'D')

    def test_file_route_no_workspace_roots_404(self):
        # 1537->1535 + 1544-1545: no repo roots AND no legacy cwd -> 404.
        with tempfile.TemporaryDirectory() as tmp:
            app = self._app_all_missing(tmp, '/nonexistent/legacy')
            response = app.test_client().get(
                '/api/sessions/T-1/file', query_string={'path': 'x.txt'},
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['error'], 'no workspace for this task')


# --------------------------------------------------------------------------
# file route: resolved.is_file() flips False between checks -> 404 (1596)
# --------------------------------------------------------------------------


class FileVanishedTests(unittest.TestCase):
    def test_file_vanishes_between_checks_returns_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'client'
            repo.mkdir()
            (repo / '.git').mkdir()
            target = repo / 'gone.txt'
            target.write_text('hi', encoding='utf-8')
            workspace = _WorkspaceManager(
                records={'T-1': SimpleNamespace(repository_ids=['client'])},
                repo_paths={('T-1', 'client'): str(repo)},
                workspace_paths={'T-1': tmp},
            )
            app = create_app(
                session_manager=_Manager(), workspace_manager=workspace,
            )
            real_is_file = Path.is_file
            seen = {'n': 0}

            def flaky_is_file(self):
                if self.name == 'gone.txt':
                    seen['n'] += 1
                    # First call (candidate loop) True; the guard at 1595
                    # sees False -> 404 at 1596.
                    return seen['n'] == 1
                return real_is_file(self)

            with patch.object(Path, 'is_file', flaky_is_file):
                response = app.test_client().get(
                    '/api/sessions/T-1/file', query_string={'path': 'gone.txt'},
                )
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.get_json()['error'], 'file not found')


# --------------------------------------------------------------------------
# post repo approvals: revoke entry that is not actually revoked (1349->1345)
# --------------------------------------------------------------------------


class RepoApprovalsRevokeMissTests(unittest.TestCase):
    def test_revoke_returning_false_is_not_listed(self):
        service = MagicMock()
        service.revoke.return_value = False  # nothing to revoke
        app = create_app(session_manager=_Manager())
        with patch(
            'kato_core_lib.data_layers.service.repository_approval_service.RepositoryApprovalService',
            return_value=service,
        ):
            body = app.test_client().post('/api/repository-approvals', json={
                'revoke': ['absent-repo'],
            }).get_json()
        self.assertEqual(body['applied']['revoked'], [])


# --------------------------------------------------------------------------
# status stream: empty backlog -> synthetic open entry (2223)
# --------------------------------------------------------------------------


class StatusStreamEmptyBacklogTests(unittest.TestCase):
    def test_empty_backlog_emits_synthetic_entry(self):
        broadcaster = MagicMock()
        broadcaster.recent.return_value = []
        broadcaster.wait_for_new.side_effect = RuntimeError('stop')
        frames = []
        gen = _status_event_stream(broadcaster)
        try:
            for frame in gen:
                frames.append(frame)
                if len(frames) > 5:  # pragma: no cover - safety bound
                    break
        except RuntimeError:
            pass
        joined = ''.join(frames)
        self.assertIn('synthetic-open', joined)
        self.assertIn('Live feed connected', joined)


# --------------------------------------------------------------------------
# events route + _event_stream_generator branches (2264-2267, 2738-2756)
# --------------------------------------------------------------------------


class _BacklogSession:
    def __init__(self, events):
        self._events = list(events)
        self.is_alive = False

    def recent_events(self):
        return list(self._events)

    def events_after(self, start_index):
        if start_index >= len(self._events):
            return ([], len(self._events))
        return (list(self._events[start_index:]), len(self._events))


class _Event:
    def __init__(self, etype):
        self.event_type = etype
        self.raw = {'type': etype}

    def to_dict(self):
        return {'raw': {'type': self.event_type}, 'received_at_epoch': 1.0}


class EventsRouteTests(unittest.TestCase):
    def test_events_route_streams_missing_for_unknown_task(self):
        app = create_app(session_manager=_Manager())
        response = app.test_client().get('/api/sessions/UNKNOWN/events')
        self.assertEqual(response.status_code, 200)
        body = b''.join(response.response)
        self.assertIn(b'session_missing', body)

    def test_generator_drains_queue_then_follows_spawned_session(self):
        # Idle path: no live session, but draining the queue starts one,
        # which is then backlog-replayed + followed to close (2737-2747).
        record = _Record(task_id='T-1')
        spawned = _BacklogSession([_Event('system')])
        manager = MagicMock()
        # First get_session -> None (idle); after drain -> the spawned one.
        manager.get_record.return_value = record
        manager.get_session.side_effect = [None, spawned, spawned]
        service = MagicMock()
        service.drain_next_queued_task_comment.return_value = {
            'ok': True, 'started': True, 'comment_id': 'c1',
        }
        with patch.object(app_module, '_resolve_agent_session_id', return_value=''):
            frames = list(_event_stream_generator(manager, None, 'T-1', service))
        joined = ''.join(frames)
        self.assertIn('session_event', joined)
        self.assertIn('session_closed', joined)

    def test_generator_follows_existing_live_session(self):
        # The fully-live path (2751-2756): a session exists from the start.
        record = _Record(task_id='T-1')
        live = _BacklogSession([_Event('system'), _Event('result')])
        manager = MagicMock()
        manager.get_record.return_value = record
        manager.get_session.return_value = live
        with patch.object(app_module, '_resolve_agent_session_id', return_value=''):
            frames = list(_event_stream_generator(manager, None, 'T-1', None))
        joined = ''.join(frames)
        self.assertIn('session_event', joined)
        self.assertIn('session_closed', joined)


# --------------------------------------------------------------------------
# _replay_preflight_log: not-callable + exception guards (2780, 2783-2784)
# --------------------------------------------------------------------------


class ReplayPreflightGuardTests(unittest.TestCase):
    def test_no_read_method_yields_nothing(self):
        workspace = SimpleNamespace()  # no read_preflight_log
        self.assertEqual(list(_replay_preflight_log(workspace, 'T-1')), [])

    def test_read_raising_yields_nothing(self):
        def boom(_):
            raise RuntimeError('cannot read')
        workspace = SimpleNamespace(read_preflight_log=boom)
        self.assertEqual(list(_replay_preflight_log(workspace, 'T-1')), [])

    def test_none_workspace_or_blank_task_yields_nothing(self):
        self.assertEqual(list(_replay_preflight_log(None, 'T-1')), [])
        ws = SimpleNamespace(read_preflight_log=lambda t: [])
        self.assertEqual(list(_replay_preflight_log(ws, '')), [])


# --------------------------------------------------------------------------
# _replay_history_from_disk: ImportError/exception/real events
# (2812-2830)
# --------------------------------------------------------------------------


class ReplayHistoryFromDiskTests(unittest.TestCase):
    def test_blank_session_id_yields_nothing(self):
        self.assertEqual(list(_replay_history_from_disk('')), [])

    def test_load_history_exception_yields_nothing(self):
        with patch(
            'claude_core_lib.claude_core_lib.session.history.load_history_events',
            side_effect=RuntimeError('disk error'),
        ):
            self.assertEqual(list(_replay_history_from_disk('sid')), [])

    def test_load_history_events_are_emitted(self):
        with patch(
            'claude_core_lib.claude_core_lib.session.history.load_history_events',
            return_value=[{'type': 'assistant'}, {'type': 'result'}],
        ):
            frames = list(_replay_history_from_disk('sid'))
        self.assertEqual(len(frames), 2)
        self.assertIn('session_history_event', ''.join(frames))

    def test_import_error_yields_nothing(self):
        # 2817-2818: the lazy ``from ...history import load_history_events``
        # raises ImportError (claude_core_lib unavailable) -> empty.
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == 'claude_core_lib.claude_core_lib.session.history' and \
                    args and args[2] and 'load_history_events' in args[2]:
                raise ImportError('claude_core_lib not installed')
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, '__import__', fake_import):
            self.assertEqual(list(_replay_history_from_disk('sid')), [])


# --------------------------------------------------------------------------
# _follow_live_session: final tail drain after is_alive flips (2878-2879)
# --------------------------------------------------------------------------


class FollowLiveTailDrainTests(unittest.TestCase):
    def test_tail_events_after_close_are_drained(self):
        session = MagicMock()
        session.is_alive = False
        # First slice: nothing. Tail slice (after is_alive False): one event.
        session.events_after.side_effect = [
            ([], 0),
            ([_Event('result')], 1),
        ]
        service = MagicMock()
        frames = list(_follow_live_session(
            session, agent_service=service, task_id='T-1',
        ))
        joined = ''.join(frames)
        self.assertIn('session_event', joined)  # drained tail event
        self.assertIn('session_closed', joined)


# --------------------------------------------------------------------------
# _drain_queued_task_comment: exception + non-dict result (2934-2941)
# --------------------------------------------------------------------------


class DrainQueuedCommentTests(unittest.TestCase):
    def test_drain_exception_returns_false(self):
        service = MagicMock()
        service.drain_next_queued_task_comment.side_effect = RuntimeError('drain boom')
        self.assertFalse(_drain_queued_task_comment(service, 'T-1'))

    def test_non_dict_result_returns_false(self):
        service = MagicMock()
        service.drain_next_queued_task_comment.return_value = 'not-a-dict'
        self.assertFalse(_drain_queued_task_comment(service, 'T-1'))

    def test_no_drain_method_returns_false(self):
        self.assertFalse(_drain_queued_task_comment(object(), 'T-1'))


# --------------------------------------------------------------------------
# _fire_webserver_hook: exception path (2402-2403)
# --------------------------------------------------------------------------


class FireWebserverHookTests(unittest.TestCase):
    def test_hook_fire_exception_is_swallowed(self):
        runner = MagicMock()
        runner.fire.side_effect = RuntimeError('hook crash')
        app = create_app(session_manager=_Manager(), hook_runner=runner)
        with app.app_context():
            # Should not raise — the exception is logged and swallowed.
            _fire_webserver_hook(app, 'stop', {'task_id': 'T-1'})
        runner.fire.assert_called_once()

    def test_no_runner_is_a_noop(self):
        app = create_app(session_manager=_Manager())
        with app.app_context():
            _fire_webserver_hook(app, 'stop', {'task_id': 'T-1'})  # no raise


# --------------------------------------------------------------------------
# _run_pre_tool_use_hook: not blocked -> (False, '') (2484)
# --------------------------------------------------------------------------


class PreToolUseNotBlockedTests(unittest.TestCase):
    def test_results_present_but_not_blocked(self):
        runner = MagicMock()
        runner.fire.return_value = [SimpleNamespace(blocked=False)]
        runner.is_blocked.return_value = False
        app = create_app(session_manager=_Manager(), hook_runner=runner)
        with app.test_request_context():
            blocked, rationale = _run_pre_tool_use_hook(app, 'T-1', {})
        self.assertFalse(blocked)
        self.assertEqual(rationale, '')


# --------------------------------------------------------------------------
# _chat_resume_context: cwd already set -> skip repo-path fallback (2641->2647)
# --------------------------------------------------------------------------


class ChatResumeContextCwdSetTests(unittest.TestCase):
    def test_cwd_from_record_skips_repo_path_lookup(self):
        record_mgr = MagicMock()
        record_mgr.get_record.return_value = SimpleNamespace(
            cwd='/record/cwd', task_summary='S',
        )
        workspace = MagicMock()
        workspace.get.return_value = SimpleNamespace(
            cwd='', task_summary='', repository_ids=['client'],
        )
        cwd, summary = _chat_resume_context(record_mgr, workspace, 'T-1')
        self.assertEqual(cwd, '/record/cwd')
        self.assertEqual(summary, 'S')
        # repository_path must NOT be consulted because cwd was non-blank.
        workspace.repository_path.assert_not_called()


# --------------------------------------------------------------------------
# _session_pending_permission_tool: live probe returns empty (3115 / 3149-3154)
# --------------------------------------------------------------------------


class PendingPermissionLiveProbeTests(unittest.TestCase):
    def test_live_probe_empty_trusts_no_pending(self):
        session = SimpleNamespace(
            pending_control_request_tool=lambda: '',
            recent_events=lambda: [  # would match if the walk ran — it must NOT
                SimpleNamespace(raw={'type': 'control_request', 'tool': 'Bash'}),
            ],
        )
        self.assertEqual(_session_pending_permission_tool(session), '')

    def test_live_probe_raises_falls_back_to_empty(self):
        def boom():
            raise RuntimeError('probe failed')
        session = SimpleNamespace(
            pending_control_request_tool=boom,
            recent_events=lambda: [],
        )
        self.assertEqual(_session_pending_permission_tool(session), '')

    def test_live_probe_returns_tool_name(self):
        session = SimpleNamespace(
            pending_control_request_tool=lambda: '  Edit  ',
            recent_events=lambda: [],
        )
        self.assertEqual(_session_pending_permission_tool(session), 'Edit')


# --------------------------------------------------------------------------
# _live_session_ids: alive vs dead sessions (3177->3176)
# --------------------------------------------------------------------------


class LiveSessionIdsTests(unittest.TestCase):
    def test_only_alive_sessions_are_collected(self):
        alive_record = _Record(task_id='ALIVE')
        dead_record = _Record(task_id='DEAD')
        manager = _Manager(
            records=[alive_record, dead_record],
            sessions={
                'ALIVE': SimpleNamespace(is_alive=True),
                'DEAD': SimpleNamespace(is_alive=False),
            },
        )
        self.assertEqual(_live_session_ids(manager), {'ALIVE'})


# --------------------------------------------------------------------------
# _workspace_record_to_dict: backfill map present but value empty (3207->3209)
# --------------------------------------------------------------------------


class WorkspaceRecordBackfillEmptyTests(unittest.TestCase):
    def test_empty_backfill_value_leaves_session_id_blank(self):
        from kato_webserver.app import _workspace_record_to_dict

        record = SimpleNamespace(
            task_id='T-1',
            to_dict=lambda: {'task_id': 'T-1', AGENT_SESSION_ID: ''},
        )
        payload = _workspace_record_to_dict(
            record,
            live_session_ids=set(),
            session_ids_by_task={'T-1': ''},  # present but empty -> no backfill
        )
        self.assertEqual(payload[AGENT_SESSION_ID], '')


# --------------------------------------------------------------------------
# main(): dev-server entrypoint (3259-3262)
# --------------------------------------------------------------------------


class MainEntrypointTests(unittest.TestCase):
    def test_main_builds_app_and_runs(self):
        # HTTPS off: this test doesn't exercise TLS and must not touch
        # the real ~/.kato/tls (ensure_local_tls_cert writes for real).
        with patch.dict(os.environ, {'KATO_WEBSERVER_HTTPS': '0'}), \
             patch.object(app_module.Flask, 'run') as run:
            main()
        run.assert_called_once()
        # Host/port come from env defaults.
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs.get('host'), '127.0.0.1')
        self.assertEqual(kwargs.get('port'), 5050)
        self.assertIsNone(kwargs.get('ssl_context'))

    def test_main_serves_https_with_a_generated_cert_by_default(self):
        with patch.dict(os.environ, {}, clear=False), \
             patch(
                 'kato_core_lib.helpers.tls_cert_utils.ensure_local_tls_cert',
                 return_value=('cert.pem', 'key.pem'),
             ), \
             patch.object(app_module.Flask, 'run') as run:
            os.environ.pop('KATO_WEBSERVER_HTTPS', None)
            main()
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs.get('ssl_context'), ('cert.pem', 'key.pem'))


# --------------------------------------------------------------------------
# _event_stream_generator: drain started but session re-fetch is still None
# (race) -> falls through to idle (2739->2748)
# --------------------------------------------------------------------------


class EventStreamDrainRaceTests(unittest.TestCase):
    def test_drain_started_but_no_session_falls_through_to_idle(self):
        record = _Record(task_id='T-1', task_summary='S')
        manager = MagicMock()
        manager.get_record.return_value = record
        # Idle at first; even after a "started" drain the re-fetch is None
        # (the run finished between drain + re-fetch).
        manager.get_session.return_value = None
        service = MagicMock()
        service.drain_next_queued_task_comment.return_value = {
            'ok': True, 'started': True, 'comment_id': 'c1',
        }
        with patch.object(app_module, '_resolve_agent_session_id', return_value=''):
            frames = list(_event_stream_generator(manager, None, 'T-1', service))
        self.assertIn('session_idle', ''.join(frames))


# --------------------------------------------------------------------------
# _pending_permission_tool_by_task: live session with empty tool (3115->3113)
# --------------------------------------------------------------------------


class PendingPermissionByTaskTests(unittest.TestCase):
    def test_empty_tool_name_is_not_recorded(self):
        from kato_webserver.app import _pending_permission_tool_by_task

        record = _Record(task_id='T-1')
        # Live probe returns '' -> _session_pending_permission_tool '' ->
        # the ``if tool_name`` guard skips the assignment (3115->3113).
        session = SimpleNamespace(pending_control_request_tool=lambda: '')
        manager = _Manager(records=[record], sessions={'T-1': session})
        self.assertEqual(_pending_permission_tool_by_task(manager), {})

    def test_non_empty_tool_name_is_recorded(self):
        from kato_webserver.app import _pending_permission_tool_by_task

        record = _Record(task_id='T-1')
        session = SimpleNamespace(pending_control_request_tool=lambda: 'Bash')
        manager = _Manager(records=[record], sessions={'T-1': session})
        self.assertEqual(_pending_permission_tool_by_task(manager), {'T-1': 'Bash'})


# --------------------------------------------------------------------------
# _follow_live_session: alive iteration without ping reaches sleep (2883->2887)
# --------------------------------------------------------------------------


class FollowLiveNoHeartbeatTests(unittest.TestCase):
    def test_alive_iteration_below_heartbeat_window_sleeps(self):
        session = MagicMock()
        alive_states = iter([True, False])
        type(session).is_alive = property(lambda self: next(alive_states, False))
        session.events_after.return_value = ([], 0)
        # monotonic stays inside the heartbeat window so the ping branch
        # is skipped (2883->2887) and the loop proceeds to time.sleep.
        clock = iter([0.0, 1.0, 1.0])

        def fake_monotonic():
            return next(clock, 1.0)

        with patch.object(app_module.time, 'monotonic', fake_monotonic), \
                patch.object(app_module.time, 'sleep') as sleep:
            frames = list(_follow_live_session(session))
        sleep.assert_called_once()
        joined = ''.join(frames)
        self.assertNotIn(': ping', joined)
        self.assertIn('session_closed', joined)


if __name__ == '__main__':
    unittest.main()
