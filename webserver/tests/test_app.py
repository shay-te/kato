import unittest
from unittest.mock import MagicMock

from kato_webserver.app import (
    _follow_live_session,
    _replay_session_backlog,
    _session_has_pending_permission,
    create_app,
)


class _FakeRecord:
    def __init__(self, **kwargs):
        self._payload = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)

    def to_dict(self):
        return dict(self._payload)


class _FakeManager:
    def __init__(self, records=None):
        self._records = records or []

    def list_records(self):
        return self._records

    def get_record(self, task_id):
        for record in self._records:
            payload = record.to_dict()
            if payload.get('task_id') == task_id:
                return record
        return None

    def get_session(self, task_id):  # noqa: ARG002
        return None


class _FakeSessionEvent:
    def __init__(self, event_type):
        self.event_type = event_type
        self.raw = {'type': event_type}

    def to_dict(self):
        return {'raw': {'type': self.event_type}, 'received_at_epoch': 1.0}


class _RaceyLiveSession:
    def __init__(self):
        self._events = [_FakeSessionEvent('system')]
        self._wait_calls = 0

    @property
    def is_alive(self):
        return False

    def recent_events(self):
        return list(self._events)

    def events_after(self, start_index):
        if start_index >= len(self._events):
            return ([], len(self._events))
        return (list(self._events[start_index:]), len(self._events))

    def wait_for_new_events(self, start_index, timeout):
        # Mirror the original race: a new event lands AFTER the
        # backlog snapshot but BEFORE the follow loop's first wakeup.
        # The follow loop must still observe + emit it before
        # reporting the session closed.
        self._wait_calls += 1
        if self._wait_calls == 1:
            self._events.append(_FakeSessionEvent('control_request'))
        new_slice = self._events[start_index:]
        return (list(new_slice), len(self._events), False)


class WebserverAppTests(unittest.TestCase):
    def setUp(self):
        self.manager = _FakeManager(records=[
            _FakeRecord(
                task_id='PROJ-1',
                task_summary='do the thing',
                status='active',
                claude_session_id='abc',
            ),
        ])
        self.app = create_app(session_manager=self.manager)
        self.client = self.app.test_client()

    def test_healthz_reports_ok(self):
        response = self.client.get('/healthz')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'status': 'ok'})

    def test_index_renders_session_card(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<div id="root"></div>', response.data)
        self.assertIn(b'/static/build/app.js', response.data)

    def test_index_renders_empty_state_when_no_sessions(self):
        empty_app = create_app(session_manager=_FakeManager(records=[]))
        client = empty_app.test_client()
        response = client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<div id="root"></div>', response.data)
        self.assertIn(b'/static/build/app.js', response.data)

    def test_session_list_endpoint_returns_serialized_records(self):
        response = self.client.get('/api/sessions')
        self.assertEqual(response.status_code, 200)
        records = response.get_json()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['task_id'], 'PROJ-1')
        self.assertEqual(records[0]['claude_session_id'], 'abc')

    def test_session_detail_endpoint_includes_recent_events_when_session_alive(self):
        live_session = MagicMock()
        live_session.is_alive = True
        live_session.recent_events.return_value = [
            MagicMock(to_dict=lambda: {'raw': {'type': 'system'}, 'received_at_epoch': 1.0}),
        ]
        manager = _FakeManager(records=[
            _FakeRecord(task_id='PROJ-2', task_summary='live', status='active',
                        claude_session_id='s'),
        ])
        manager.get_session = lambda task_id: live_session if task_id == 'PROJ-2' else None
        app = create_app(session_manager=manager)
        response = app.test_client().get('/api/sessions/PROJ-2')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['task_id'], 'PROJ-2')
        self.assertEqual(len(payload['recent_events']), 1)
        self.assertEqual(payload['recent_events'][0]['raw']['type'], 'system')

    def test_session_detail_endpoint_returns_404_for_unknown_task(self):
        response = self.client.get('/api/sessions/PROJ-99')
        self.assertEqual(response.status_code, 404)

    def test_claude_sessions_endpoint_lists_metadata_from_disk(self):
        # Stand up a temp Claude sessions root with one transcript
        # the endpoint can discover. Stub the session manager to
        # report no existing kato adoption so the response shape is
        # the simple case.
        import json, os, tempfile, unittest.mock as _mock
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / '-Users-dev-myproj'
            project_dir.mkdir()
            (project_dir / 'sess-1.jsonl').write_text(
                json.dumps({
                    'type': 'user',
                    'sessionId': 'sess-1',
                    'cwd': '/Users/dev/myproj',
                    'message': {'content': 'help with auth'},
                }) + '\n',
                encoding='utf-8',
            )
            with _mock.patch.dict(
                os.environ,
                {'KATO_CLAUDE_SESSIONS_ROOT': str(root)},
                clear=False,
            ):
                response = self.client.get('/api/claude/sessions')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload['sessions']), 1)
        row = payload['sessions'][0]
        self.assertEqual(row['session_id'], 'sess-1')
        self.assertEqual(row['cwd'], '/Users/dev/myproj')
        self.assertEqual(row['first_user_message'], 'help with auth')
        # No kato task has adopted this session id.
        self.assertEqual(row['adopted_by_task_id'], '')

    def test_claude_sessions_endpoint_marks_adopted_sessions(self):
        # PROJ-1 in the fixture already has claude_session_id='abc'.
        # If we put a transcript with that id on disk, the endpoint
        # should report it as adopted by PROJ-1.
        import json, os, tempfile, unittest.mock as _mock
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / '-proj'
            project_dir.mkdir()
            (project_dir / 'abc.jsonl').write_text(
                json.dumps({
                    'type': 'user',
                    'sessionId': 'abc',
                    'cwd': '/proj',
                    'message': {'content': 'hello'},
                }) + '\n',
                encoding='utf-8',
            )
            with _mock.patch.dict(
                os.environ,
                {'KATO_CLAUDE_SESSIONS_ROOT': str(root)},
                clear=False,
            ):
                response = self.client.get('/api/claude/sessions')
        self.assertEqual(response.status_code, 200)
        rows = response.get_json()['sessions']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['adopted_by_task_id'], 'PROJ-1')

    def test_adopt_claude_session_endpoint_calls_manager(self):
        adopted: list[tuple[str, str]] = []

        class _RecordingManager(_FakeManager):
            def adopt_session_id(self, task_id, *, claude_session_id, task_summary=''):
                adopted.append((task_id, claude_session_id))
                return _FakeRecord(
                    task_id=task_id,
                    claude_session_id=claude_session_id,
                )

        manager = _RecordingManager()
        app = create_app(session_manager=manager)
        response = app.test_client().post(
            '/api/sessions/PROJ-7/adopt-claude-session',
            json={'claude_session_id': 'imported-sess-id'},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['task_id'], 'PROJ-7')
        self.assertEqual(payload['claude_session_id'], 'imported-sess-id')
        self.assertEqual(adopted, [('PROJ-7', 'imported-sess-id')])

    def test_adopt_claude_session_endpoint_rejects_empty_id(self):
        response = self.client.post(
            '/api/sessions/PROJ-1/adopt-claude-session',
            json={'claude_session_id': '   '},
        )
        self.assertEqual(response.status_code, 400)

    def test_adopt_claude_session_endpoint_migrates_jsonl_into_target_cwd(self):
        # End-to-end: adopt + migrate. Source JSONL lives under the
        # dev's checkout cwd; after adoption, a copy must exist under
        # the kato workspace cwd's project directory so
        # ``claude --resume <id>`` finds it on the next spawn.
        import json, os, tempfile, unittest.mock as _mock
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            sessions_root = Path(tmp)
            # Source: VS-Code-style session under the dev's path.
            source_dir = sessions_root / '-Users-dev-repos-myproj'
            source_dir.mkdir()
            source_path = source_dir / 'sess-imported.jsonl'
            source_path.write_text(
                json.dumps({'type': 'user', 'sessionId': 'sess-imported',
                            'cwd': '/Users/dev/repos/myproj'}) + '\n',
                encoding='utf-8',
            )

            class _AdoptingManager(_FakeManager):
                def __init__(self):
                    super().__init__(records=[_FakeRecord(
                        task_id='PROJ-9',
                        cwd='/Users/dev/.kato/workspaces/PROJ-9/myproj',
                        claude_session_id='',
                    )])

                def get_record(self, task_id):
                    return next(
                        (r for r in self._records if r.task_id == task_id),
                        None,
                    )

                def adopt_session_id(self, task_id, *, claude_session_id, task_summary=''):
                    return _FakeRecord(
                        task_id=task_id,
                        claude_session_id=claude_session_id,
                    )

            manager = _AdoptingManager()
            with _mock.patch.dict(
                os.environ,
                {'KATO_CLAUDE_SESSIONS_ROOT': str(sessions_root)},
                clear=False,
            ):
                app = create_app(session_manager=manager)
                response = app.test_client().post(
                    '/api/sessions/PROJ-9/adopt-claude-session',
                    json={'claude_session_id': 'sess-imported'},
                )
            self.assertEqual(response.status_code, 200)
            # The JSONL has been copied into the kato cwd's project dir.
            # Claude Code's encoding only replaces ``/`` with ``-`` —
            # dots in segments (``.kato``) are preserved.
            kato_dir = sessions_root / '-Users-dev-.kato-workspaces-PROJ-9-myproj'
            self.assertTrue((kato_dir / 'sess-imported.jsonl').is_file())
            payload = response.get_json()
            self.assertIn('transcript_migrated_to', payload)
            self.assertIn('PROJ-9', payload['transcript_migrated_to'])

    def test_post_message_forwards_images_to_live_session(self):
        live = MagicMock()
        live.is_alive = True
        send_calls = []
        def record_send(text, images=None):
            send_calls.append((text, images))
        live.send_user_message.side_effect = record_send

        class _LiveManager(_FakeManager):
            def get_session(self, task_id):
                return live if task_id == 'PROJ-1' else None

        manager = _LiveManager(records=[
            _FakeRecord(task_id='PROJ-1', claude_session_id='abc'),
        ])
        app = create_app(session_manager=manager)
        response = app.test_client().post(
            '/api/sessions/PROJ-1/messages',
            json={
                'text': 'look at this',
                'images': [
                    {'media_type': 'image/png', 'data': 'AAAA'},
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(send_calls), 1)
        self.assertEqual(send_calls[0][0], 'look at this')
        self.assertEqual(len(send_calls[0][1]), 1)
        self.assertEqual(send_calls[0][1][0]['media_type'], 'image/png')

    def test_post_message_accepts_images_only_no_text(self):
        live = MagicMock()
        live.is_alive = True
        send_calls = []
        def record_send(text, images=None):
            send_calls.append((text, images))
        live.send_user_message.side_effect = record_send

        class _LiveManager(_FakeManager):
            def get_session(self, task_id):
                return live if task_id == 'PROJ-1' else None

        manager = _LiveManager(records=[
            _FakeRecord(task_id='PROJ-1', claude_session_id='abc'),
        ])
        app = create_app(session_manager=manager)
        response = app.test_client().post(
            '/api/sessions/PROJ-1/messages',
            json={
                'text': '',
                'images': [{'media_type': 'image/png', 'data': 'BBBB'}],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(send_calls[0][0], '')
        self.assertEqual(len(send_calls[0][1]), 1)

    def test_post_message_400_when_neither_text_nor_images(self):
        live = MagicMock()
        live.is_alive = True
        class _LiveManager(_FakeManager):
            def get_session(self, task_id):
                return live if task_id == 'PROJ-1' else None
        manager = _LiveManager()
        app = create_app(session_manager=manager)
        response = app.test_client().post(
            '/api/sessions/PROJ-1/messages',
            json={'text': '   ', 'images': []},
        )
        self.assertEqual(response.status_code, 400)

    def test_post_message_falls_back_when_session_lacks_images_kwarg(self):
        # Older session implementation predating the images kwarg —
        # the endpoint retries text-only so a stale dependency
        # doesn't break the message path.
        live = MagicMock()
        live.is_alive = True
        sent = []
        def picky_send(text, **kwargs):
            if 'images' in kwargs:
                raise TypeError("unexpected keyword argument 'images'")
            sent.append(text)
        live.send_user_message.side_effect = picky_send

        class _LiveManager(_FakeManager):
            def get_session(self, task_id):
                return live if task_id == 'PROJ-1' else None
        manager = _LiveManager(records=[
            _FakeRecord(task_id='PROJ-1', claude_session_id='abc'),
        ])
        app = create_app(session_manager=manager)
        response = app.test_client().post(
            '/api/sessions/PROJ-1/messages',
            json={'text': 'hi', 'images': [{'media_type': 'image/png', 'data': 'AAAA'}]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sent, ['hi'])

    def test_adopt_claude_session_endpoint_refuses_when_session_alive(self):
        live = MagicMock()
        live.is_alive = True

        class _LiveManager(_FakeManager):
            def get_session(self, task_id):
                return live if task_id == 'PROJ-1' else None

            def adopt_session_id(self, *args, **kwargs):  # pragma: no cover
                raise AssertionError('should not be called when live')

        manager = _LiveManager(records=[
            _FakeRecord(task_id='PROJ-1', claude_session_id='existing'),
        ])
        app = create_app(session_manager=manager)
        response = app.test_client().post(
            '/api/sessions/PROJ-1/adopt-claude-session',
            json={'claude_session_id': 'new'},
        )
        self.assertEqual(response.status_code, 409)

    def test_live_stream_does_not_skip_event_created_between_backlog_and_follow(self):
        session = _RaceyLiveSession()
        backlog = _replay_session_backlog(session)
        frames = []
        try:
            while True:
                frames.append(next(backlog))
        except StopIteration as exc:
            replayed_count = exc.value

        follow = _follow_live_session(session, start_index=replayed_count)
        frames.append(next(follow))

        joined = ''.join(frames)
        self.assertIn('"type": "system"', joined)
        self.assertIn('"type": "control_request"', joined)

    def test_session_pending_permission_detects_unanswered_request(self):
        # Live state ("agent is paused on stdin") is the new
        # primary source of truth — when ``pending_control_request_tool``
        # returns a non-empty string, we trust it. Earlier this
        # method walked ``recent_events`` history; that fallback
        # is still wired for stubs that don't expose the live probe.
        session = MagicMock()
        session.pending_control_request_tool = MagicMock(return_value='Bash')
        session.recent_events.return_value = [
            _FakeSessionEvent('assistant'),
            _FakeSessionEvent('control_request'),
        ]

        self.assertTrue(_session_has_pending_permission(session))

    def test_session_pending_permission_clears_after_response(self):
        # ``send_permission_response`` pops the request from
        # ``_pending_control_requests``, so the live probe returns
        # '' the instant a response lands. Tab-orange clears
        # immediately even if the synthetic response didn't make
        # it back to the history walk.
        session = MagicMock()
        session.pending_control_request_tool = MagicMock(return_value='')
        session.recent_events.return_value = [
            _FakeSessionEvent('control_request'),
            _FakeSessionEvent('permission_response'),
        ]

        self.assertFalse(_session_has_pending_permission(session))

    def test_session_pending_permission_falls_back_to_history_walk_when_no_live_probe(self):
        # Older session stubs / custom backends without the live
        # probe still get the legacy history-walk behaviour, so we
        # never silently start reporting "no pending" on a backend
        # that hasn't migrated.
        from types import SimpleNamespace
        session = SimpleNamespace(
            recent_events=lambda: [
                _FakeSessionEvent('assistant'),
                _FakeSessionEvent('control_request'),
            ],
        )
        self.assertTrue(_session_has_pending_permission(session))

    def test_session_pending_permission_live_probe_wins_over_stale_history(self):
        # The original bug: ``recent_events`` had an old
        # ``control_request`` whose response was dedupe'd or never
        # logged, so the history walk reported "still waiting"
        # forever and the tab stayed orange. The live probe says
        # "nothing pending" and is now authoritative — history
        # is NOT consulted when the live probe returns ''.
        session = MagicMock()
        session.pending_control_request_tool = MagicMock(return_value='')
        session.recent_events.return_value = [
            _FakeSessionEvent('control_request'),
            _FakeSessionEvent('assistant'),
            _FakeSessionEvent('control_request'),
        ]

        self.assertFalse(_session_has_pending_permission(session))

    def test_session_list_endpoint_marks_pending_permission_without_workspace(self):
        live_session = MagicMock()
        live_session.is_alive = True
        live_session.is_working = False
        live_session.recent_events.return_value = [_FakeSessionEvent('control_request')]
        manager = _FakeManager(records=[
            _FakeRecord(
                task_id='PROJ-3',
                task_summary='approval',
                status='active',
                claude_session_id='s',
            ),
        ])
        manager.get_session = lambda task_id: live_session if task_id == 'PROJ-3' else None
        app = create_app(session_manager=manager)
        response = app.test_client().get('/api/sessions')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload[0]['has_pending_permission'])


class _FakeWorkspaceRecord:
    def __init__(self, **payload):
        self._payload = payload
        self.task_id = payload.get('task_id', '')
        self.repository_ids = payload.get('repository_ids', [])

    def to_dict(self):
        return dict(self._payload)


class _FakeWorkspaceManager:
    """Minimal stand-in for ``WorkspaceManager`` for the multi-repo routes."""

    def __init__(self, records, *, repo_paths=None):
        self._records = records
        self._repo_paths = repo_paths or {}

    def list_workspaces(self):
        return list(self._records)

    def get(self, task_id):
        for record in self._records:
            if record.task_id == task_id:
                return record
        return None

    def repository_path(self, task_id, repo_id):
        from pathlib import Path
        return Path(self._repo_paths.get((task_id, repo_id), '/missing'))


class _FakeRecordWithCwd(_FakeRecord):
    def __init__(self, **payload):
        super().__init__(**payload)
        self.task_id = payload.get('task_id', '')


class MultiRepoEndpointShapeTests(unittest.TestCase):
    """The Files / Diff endpoints must now surface every repo per task."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.repo_a = self.tmp_root / 'PROJ-1' / 'client'
        self.repo_b = self.tmp_root / 'PROJ-1' / 'backend'
        for repo in (self.repo_a, self.repo_b):
            (repo / '.git').mkdir(parents=True)

        # The session manager only owns the legacy single cwd; the
        # workspace manager carries the multi-repo list.
        self.session_manager = _FakeManager(records=[
            _FakeRecordWithCwd(
                task_id='PROJ-1',
                task_summary='multi-repo task',
                status='active',
                claude_session_id='abc',
                cwd=str(self.repo_a),
            ),
        ])
        self.workspace_manager = _FakeWorkspaceManager(
            records=[
                _FakeWorkspaceRecord(
                    task_id='PROJ-1',
                    task_summary='multi-repo task',
                    status='active',
                    repository_ids=['client', 'backend'],
                ),
            ],
            repo_paths={
                ('PROJ-1', 'client'): str(self.repo_a),
                ('PROJ-1', 'backend'): str(self.repo_b),
            },
        )
        self.app = create_app(
            session_manager=self.session_manager,
            workspace_manager=self.workspace_manager,
        )
        self.client = self.app.test_client()

    def test_files_endpoint_returns_one_tree_per_repo(self):
        response = self.client.get('/api/sessions/PROJ-1/files')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['repository_ids'], ['client', 'backend'])
        repo_ids_in_trees = [entry['repo_id'] for entry in payload['trees']]
        self.assertEqual(repo_ids_in_trees, ['client', 'backend'])
        # Legacy fields are still populated for old clients.
        self.assertEqual(payload['cwd'], str(self.repo_a))

    def test_session_list_endpoint_marks_inactive_workspace_pending_permission(self):
        live_session = MagicMock()
        live_session.is_alive = True
        live_session.is_working = False
        live_session.recent_events.return_value = [_FakeSessionEvent('control_request')]
        self.session_manager.get_session = (
            lambda task_id: live_session if task_id == 'PROJ-1' else None
        )
        response = self.client.get('/api/sessions')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload[0]['has_pending_permission'])
        self.assertFalse(payload[0]['working'])

    def test_diff_endpoint_returns_one_diff_entry_per_repo(self):
        # Patch git helpers so we don't need a real upstream remote.
        from unittest.mock import patch
        with patch(
            'kato_webserver.app.detect_default_branch',
            return_value='master',
        ), patch(
            'kato_webserver.app.current_branch',
            return_value='UNA-1',
        ), patch(
            'kato_webserver.app.diff_against_base',
            return_value='',
        ):
            response = self.client.get('/api/sessions/PROJ-1/diff')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['repository_ids'], ['client', 'backend'])
        repo_ids_in_diffs = [entry['repo_id'] for entry in payload['diffs']]
        self.assertEqual(repo_ids_in_diffs, ['client', 'backend'])
        self.assertEqual(payload['repo_id'], 'client')  # legacy scalar
        self.assertEqual(payload['base'], 'master')
        self.assertEqual(payload['head'], 'UNA-1')

    def test_diff_endpoint_records_error_when_default_branch_unknown(self):
        # ``detect_default_branch`` returning empty must not crash the
        # endpoint — the affected repo's accordion section gets an
        # ``error`` field and the rest still ship.
        from unittest.mock import patch

        def _branch_for(cwd: str) -> str:
            return 'master' if cwd == str(self.repo_a) else ''

        with patch(
            'kato_webserver.app.detect_default_branch',
            side_effect=_branch_for,
        ), patch(
            'kato_webserver.app.current_branch',
            return_value='UNA-1',
        ), patch(
            'kato_webserver.app.diff_against_base',
            return_value='',
        ):
            response = self.client.get('/api/sessions/PROJ-1/diff')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        client_diff = next(d for d in payload['diffs'] if d['repo_id'] == 'client')
        backend_diff = next(d for d in payload['diffs'] if d['repo_id'] == 'backend')
        self.assertEqual(client_diff['error'], '')
        self.assertEqual(backend_diff['error'], 'could not detect default branch')


if __name__ == '__main__':
    unittest.main()
