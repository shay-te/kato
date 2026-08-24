import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from agent_core_lib.agent_core_lib.helpers.session_id_utils import AGENT_SESSION_ID
from kato_webserver.app import (
    DEFAULT_CHAT_EFFORT,
    _advance_task_comments_after_result,
    _complete_in_progress_task_comments,
    _configured_chat_effort,
    _drain_queued_task_comment,
    _effort_change_needs_respawn,
    _event_stream_generator,
    _follow_live_session,
    _replay_session_backlog,
    _task_repository_ids,
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
    def __init__(self, event_type, request_id=''):
        self.event_type = event_type
        self._request_id = str(request_id or '')
        self.raw = self._build_raw()

    def _build_raw(self):
        raw = {'type': self.event_type}
        if self._request_id:
            raw['request_id'] = self._request_id
        return raw

    def to_dict(self):
        return {'raw': self._build_raw(), 'received_at_epoch': 1.0}


class _RaceyLiveSession:
    def __init__(self):
        self._events = [_FakeSessionEvent('system')]
        self._slice_calls = 0

    @property
    def is_alive(self):
        return False

    def recent_events(self):
        return list(self._events)

    def events_after(self, start_index):
        # Mirror the original race: a new event lands AFTER the
        # backlog snapshot but BEFORE the follow loop's first
        # ``events_after`` call. The follow loop must still observe
        # + emit it before reporting the session closed.
        self._slice_calls += 1
        if self._slice_calls == 1:
            self._events.append(_FakeSessionEvent('control_request'))
        if start_index >= len(self._events):
            return ([], len(self._events))
        return (list(self._events[start_index:]), len(self._events))


class WebserverAppTests(unittest.TestCase):
    def setUp(self):
        self.manager = _FakeManager(records=[
            _FakeRecord(
                task_id='PROJ-1',
                task_summary='do the thing',
                status='active',
                agent_session_id='abc',
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

    def test_static_bundles_are_cache_busted_with_mtime(self):
        # The unhashed app.js / app.css must carry a ``?v=<mtime>``
        # query so a rebuilt bundle isn't masked by the browser cache
        # (the recurring "my change isn't showing" trap).
        body = self.client.get('/').data.decode('utf-8')
        for asset in ('build/app.js', 'build/app.css', 'css/app.css'):
            match = re.search(
                rf'/static/{re.escape(asset)}\?v=(\d+)', body,
            )
            self.assertIsNotNone(
                match, f'{asset} is not cache-busted in index.html',
            )
            self.assertGreater(int(match.group(1)), 0)

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
        self.assertEqual(records[0][AGENT_SESSION_ID], 'abc')

    def test_session_detail_endpoint_includes_recent_events_when_session_alive(self):
        live_session = MagicMock()
        live_session.is_alive = True
        live_session.recent_events.return_value = [
            MagicMock(to_dict=lambda: {'raw': {'type': 'system'}, 'received_at_epoch': 1.0}),
        ]
        manager = _FakeManager(records=[
            _FakeRecord(task_id='PROJ-2', task_summary='live', status='active',
                        agent_session_id='s'),
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
                {'CLAUDE_SESSIONS_ROOT': str(root)},
                clear=False,
            ):
                response = self.client.get('/api/claude/sessions')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload['sessions']), 1)
        row = payload['sessions'][0]
        self.assertEqual(row[AGENT_SESSION_ID], 'sess-1')
        self.assertEqual(row['cwd'], '/Users/dev/myproj')
        self.assertEqual(row['first_user_message'], 'help with auth')
        # No kato task has adopted this session id.
        self.assertEqual(row['adopted_by_task_id'], '')

    def test_claude_sessions_endpoint_marks_adopted_sessions(self):
        # PROJ-1 in the fixture already has agent_session_id='abc'.
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
                {'CLAUDE_SESSIONS_ROOT': str(root)},
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
            def adopt_session_id(self, task_id, *, agent_session_id, task_summary=''):
                adopted.append((task_id, agent_session_id))
                return _FakeRecord(
                    task_id=task_id,
                    agent_session_id=agent_session_id,
                )

        manager = _RecordingManager()
        app = create_app(session_manager=manager)
        response = app.test_client().post(
            '/api/sessions/PROJ-7/adopt-agent-session',
            json={AGENT_SESSION_ID: 'imported-sess-id'},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['task_id'], 'PROJ-7')
        self.assertEqual(payload[AGENT_SESSION_ID], 'imported-sess-id')
        self.assertEqual(adopted, [('PROJ-7', 'imported-sess-id')])

    def test_adopt_claude_session_endpoint_rejects_empty_id(self):
        response = self.client.post(
            '/api/sessions/PROJ-1/adopt-agent-session',
            json={AGENT_SESSION_ID: '   '},
        )
        self.assertEqual(response.status_code, 400)

    def test_list_task_chats_orders_active_first_then_newest_detached(self):
        manager = _FakeManager(records=[_FakeRecord(
            task_id='PROJ-1',
            agent_session_id='cur',
            previous_session_ids=['old-1', 'old-2'],
        )])
        app = create_app(session_manager=manager)
        with unittest.mock.patch.dict(
            'os.environ', {'CLAUDE_SESSIONS_ROOT': tempfile.mkdtemp()},
        ):
            response = app.test_client().get('/api/sessions/PROJ-1/chats')
        self.assertEqual(response.status_code, 200)
        chats = response.get_json()['chats']
        self.assertEqual(
            [c[AGENT_SESSION_ID] for c in chats], ['cur', 'old-2', 'old-1'],
        )
        self.assertEqual([c['active'] for c in chats], [True, False, False])

    def test_list_task_chats_unknown_task_is_empty(self):
        response = self.client.get('/api/sessions/NOPE/chats')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['chats'], [])

    def test_start_task_chat_fresh_calls_manager_with_blank_id(self):
        calls = []

        class _ChatManager(_FakeManager):
            def start_new_chat(self, task_id, *, agent_session_id=''):
                calls.append((task_id, agent_session_id))
                return _FakeRecord(
                    task_id=task_id,
                    agent_session_id='',
                    previous_session_ids=['cur'],
                )

        manager = _ChatManager(records=[_FakeRecord(
            task_id='PROJ-1', agent_session_id='cur', previous_session_ids=[],
        )])
        app = create_app(session_manager=manager)
        response = app.test_client().post('/api/sessions/PROJ-1/chats', json={})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload[AGENT_SESSION_ID], '')
        self.assertEqual(payload['previous_session_ids'], ['cur'])
        self.assertEqual(calls, [('PROJ-1', '')])

    def test_start_task_chat_switches_to_a_previous_chat(self):
        calls = []

        class _ChatManager(_FakeManager):
            def start_new_chat(self, task_id, *, agent_session_id=''):
                calls.append((task_id, agent_session_id))
                return _FakeRecord(
                    task_id=task_id,
                    agent_session_id=agent_session_id,
                    previous_session_ids=['cur'],
                )

        manager = _ChatManager(records=[_FakeRecord(
            task_id='PROJ-1',
            agent_session_id='cur',
            previous_session_ids=['old-1'],
        )])
        app = create_app(session_manager=manager)
        response = app.test_client().post(
            '/api/sessions/PROJ-1/chats', json={AGENT_SESSION_ID: 'old-1'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()[AGENT_SESSION_ID], 'old-1')
        self.assertEqual(calls, [('PROJ-1', 'old-1')])

    def test_start_task_chat_rejects_a_foreign_session_id(self):
        # External sessions go through adopt — the chats switch only
        # navigates between THIS task's own conversations.
        response = self.client.post(
            '/api/sessions/PROJ-1/chats',
            json={AGENT_SESSION_ID: 'not-one-of-ours'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('adopt', response.get_json()['error'])

    def test_start_task_chat_no_record_fresh_chat_is_a_noop_success(self):
        # "New chat" on a task that never had a chat: nothing to detach —
        # the first message will spawn fresh anyway, so a 404 would just
        # read as breakage to the operator.
        response = self.client.post('/api/sessions/NOPE/chats', json={})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload[AGENT_SESSION_ID], '')
        self.assertEqual(payload['previous_session_ids'], [])

    def test_start_task_chat_no_record_switch_is_404(self):
        # Switching to a named chat NEEDS a record — that id can't be ours.
        response = self.client.post(
            '/api/sessions/NOPE/chats', json={AGENT_SESSION_ID: 'some-id'},
        )
        self.assertEqual(response.status_code, 404)

    def _chat_switch_response_with_comment(self, kato_status):
        class _BusyAgentService:
            def list_task_comments(self, task_id):  # noqa: ARG002
                return [{'id': 'c1', 'kato_status': kato_status}]

        class _SwitchableManager(_FakeManager):
            def start_new_chat(self, task_id, *, agent_session_id=''):
                return _FakeRecord(
                    task_id=task_id,
                    agent_session_id=agent_session_id,
                    previous_session_ids=['cur'],
                )

        manager = _SwitchableManager(records=[_FakeRecord(
            task_id='PROJ-1', agent_session_id='cur', previous_session_ids=[],
        )])
        app = create_app(session_manager=manager)
        app.config['AGENT_SERVICE'] = _BusyAgentService()
        return app.test_client().post('/api/sessions/PROJ-1/chats', json={})

    def test_start_task_chat_refuses_during_an_in_progress_comment_run(self):
        # Switching kills the live subprocess; mid comment-run that would
        # requeue the comment and redispatch it INTO the operator's new
        # chat. The endpoint refuses with 409 instead.
        response = self._chat_switch_response_with_comment('in_progress')
        self.assertEqual(response.status_code, 409)
        self.assertIn('review comment', response.get_json()['error'])
        # Stopping the session does NOT cancel a comment-run (the watcher
        # respawns it), so the message must not suggest it as a way out.
        self.assertNotIn('stop the session', response.get_json()['error'])

    def test_start_task_chat_refuses_while_a_comment_is_queued(self):
        # A QUEUED comment is dispatched by the next 2s watcher tick — on a
        # just-detached blank record it would spawn a fresh session whose id
        # becomes the operator's "new chat". Queued blocks the switch too.
        response = self._chat_switch_response_with_comment('queued')
        self.assertEqual(response.status_code, 409)

    def test_start_task_chat_allows_switch_with_only_settled_comments(self):
        # Done/failed/addressed comments don't occupy the session — they
        # must not brick the chats menu.
        response = self._chat_switch_response_with_comment('addressed')
        self.assertEqual(response.status_code, 200)

    def test_adopt_claude_session_endpoint_rejects_pinned_id_change(self):
        class _PinnedManager(_FakeManager):
            def adopt_session_id(self, task_id, *, agent_session_id, task_summary=''):
                raise RuntimeError(
                    'cannot adopt session id new for task PROJ-1: '
                    'existing session id old is already pinned'
                )

        app = create_app(session_manager=_PinnedManager())
        response = app.test_client().post(
            '/api/sessions/PROJ-1/adopt-agent-session',
            json={AGENT_SESSION_ID: 'new'},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn('already pinned', response.get_json()['error'])

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
                        agent_session_id='',
                    )])

                def get_record(self, task_id):
                    return next(
                        (r for r in self._records if r.task_id == task_id),
                        None,
                    )

                def adopt_session_id(self, task_id, *, agent_session_id, task_summary=''):
                    return _FakeRecord(
                        task_id=task_id,
                        agent_session_id=agent_session_id,
                    )

            manager = _AdoptingManager()
            with _mock.patch.dict(
                os.environ,
                {'CLAUDE_SESSIONS_ROOT': str(sessions_root)},
                clear=False,
            ):
                app = create_app(session_manager=manager)
                response = app.test_client().post(
                    '/api/sessions/PROJ-9/adopt-agent-session',
                    json={AGENT_SESSION_ID: 'sess-imported'},
                )
            self.assertEqual(response.status_code, 200)
            # The JSONL has been copied into the kato cwd's project dir.
            # Claude Code's encoding flattens ``/``, ``_`` and ``.`` to ``-`` —
            # ``.kato`` becomes ``-kato`` (leading dot stripped to dash).
            kato_dir = sessions_root / '-Users-dev--kato-workspaces-PROJ-9-myproj'
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
            _FakeRecord(task_id='PROJ-1', agent_session_id='abc'),
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

    def test_post_message_captures_prompt_lesson_candidate(self):
        live = MagicMock()
        live.is_alive = True
        live.send_user_message = MagicMock()

        class _LiveManager(_FakeManager):
            def get_session(self, task_id):
                return live if task_id == 'PROJ-1' else None

        manager = _LiveManager(records=[
            _FakeRecord(task_id='PROJ-1', agent_session_id='abc'),
        ])
        agent_service = MagicMock()
        app = create_app(session_manager=manager, agent_service=agent_service)
        response = app.test_client().post(
            '/api/sessions/PROJ-1/messages',
            json={'text': 'please learn from this prompt'},
        )

        self.assertEqual(response.status_code, 200)
        agent_service.lessons.capture_prompt_lesson_candidate.assert_called_once_with(
            'PROJ-1',
            'please learn from this prompt',
        )

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
            _FakeRecord(task_id='PROJ-1', agent_session_id='abc'),
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
            _FakeRecord(task_id='PROJ-1', agent_session_id='abc'),
        ])
        app = create_app(session_manager=manager)
        response = app.test_client().post(
            '/api/sessions/PROJ-1/messages',
            json={'text': 'hi', 'images': [{'media_type': 'image/png', 'data': 'AAAA'}]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sent, ['hi'])

    def test_post_message_respawns_when_live_agent_session_id_drifted(self):
        import tempfile
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )

        class _WrongLiveSession:
            def __init__(self):
                self.agent_session_id = 'wrong-live-id'
                self.is_alive = True
                self.sent = []
                self.terminate_calls = 0

            def send_user_message(self, text, images=None):
                self.sent.append((text, images))

            def terminate(self):
                self.terminate_calls += 1
                self.is_alive = False

        class _RecordingRunner:
            def __init__(self):
                self.calls = []

            def resume_session_for_chat(self, **kwargs):
                self.calls.append(kwargs)

        with tempfile.TemporaryDirectory() as state_dir:
            manager = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=lambda **_: None,
            )
            manager.adopt_session_id('PROJ-1', agent_session_id='pinned-id')
            wrong = _WrongLiveSession()
            manager._sessions[manager._lookup_key('PROJ-1')] = wrong
            runner = _RecordingRunner()
            app = create_app(
                session_manager=manager,
                planning_session_runner=runner,
            )

            response = app.test_client().post(
                '/api/sessions/PROJ-1/messages',
                json={'text': 'wake up'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['status'], 'spawned')
        self.assertEqual(wrong.sent, [])
        self.assertEqual(wrong.terminate_calls, 1)
        self.assertEqual(runner.calls[0]['task_id'], 'PROJ-1')
        self.assertEqual(runner.calls[0]['message'], 'wake up')

    def test_adopt_claude_session_endpoint_refuses_when_session_alive(self):
        live = MagicMock()
        live.is_alive = True

        class _LiveManager(_FakeManager):
            def get_session(self, task_id):
                return live if task_id == 'PROJ-1' else None

            def adopt_session_id(self, *args, **kwargs):  # pragma: no cover
                raise AssertionError('should not be called when live')

        manager = _LiveManager(records=[
            _FakeRecord(task_id='PROJ-1', agent_session_id='existing'),
        ])
        app = create_app(session_manager=manager)
        response = app.test_client().post(
            '/api/sessions/PROJ-1/adopt-agent-session',
            json={AGENT_SESSION_ID: 'new'},
        )
        self.assertEqual(response.status_code, 409)

    def test_backlog_replays_pending_permission_for_a_reconnecting_client(self):
        # Guards the backend half of the "permission dialog doesn't
        # show until I re-click the tab" fix: the per-task SSE is
        # closed on idle, so when a permission request arrives the
        # client must REOPEN the stream — and on reopen the backlog
        # replay MUST re-emit the still-pending permission_request,
        # otherwise the reconnect would show nothing.
        class _SessionWithPendingPermission:
            def recent_events(self):
                return [
                    _FakeSessionEvent('system'),
                    _FakeSessionEvent('assistant'),
                    _FakeSessionEvent('permission_request'),
                ]

        frames = []
        gen = _replay_session_backlog(_SessionWithPendingPermission())
        try:
            while True:
                frames.append(next(gen))
        except StopIteration as exc:
            replayed_count = exc.value

        joined = ''.join(frames)
        self.assertIn('"type": "permission_request"', joined)
        self.assertEqual(replayed_count, 3)

    def test_backlog_replays_pending_control_request_for_a_reconnecting_client(self):
        class _SessionWithPendingControlRequest:
            def recent_events(self):
                return [
                    _FakeSessionEvent('system'),
                    _FakeSessionEvent('control_request'),
                ]

        frames = []
        gen = _replay_session_backlog(_SessionWithPendingControlRequest())
        try:
            while True:
                frames.append(next(gen))
        except StopIteration as exc:
            replayed_count = exc.value

        joined = ''.join(frames)
        self.assertIn('"type": "control_request"', joined)
        self.assertEqual(replayed_count, 2)

    def test_backlog_drops_an_already_answered_control_request(self):
        # Regression: switching back to a task's tab reconnects the SSE and
        # replays the backlog. An ALREADY-ANSWERED control_request must NOT be
        # re-emitted — it re-pops the permission modal for a decision the
        # operator already made, every time they switch to the task, even
        # when the session is idle. A STILL-pending ask must still replay.
        class _SessionWithOneAnsweredOnePending:
            def recent_events(self):
                return [
                    _FakeSessionEvent('system'),
                    _FakeSessionEvent('control_request', request_id='answered-1'),
                    _FakeSessionEvent('control_request', request_id='pending-2'),
                ]

            def pending_control_requests(self):
                # 'answered-1' was popped when the operator answered it; only
                # 'pending-2' is still waiting.
                return [{
                    'type': 'control_request',
                    'request_id': 'pending-2',
                    'request': {},
                }]

        frames = []
        gen = _replay_session_backlog(_SessionWithOneAnsweredOnePending())
        try:
            while True:
                frames.append(next(gen))
        except StopIteration as exc:
            replayed_count = exc.value

        joined = ''.join(frames)
        self.assertIn('pending-2', joined)        # still-pending ask IS replayed
        self.assertNotIn('answered-1', joined)    # answered ask is dropped
        self.assertEqual(replayed_count, 3)       # count reflects full backlog

    def test_backlog_replays_control_request_when_session_cannot_report_pending(self):
        # Fail-open: a session with no ``pending_control_requests`` (older
        # transports, test stubs) must NOT have its control_request suppressed —
        # dropping a genuinely-pending ask the operator never sees is worse.
        class _SessionNoPendingApi:
            def recent_events(self):
                return [_FakeSessionEvent('control_request', request_id='x-1')]

        frames = []
        gen = _replay_session_backlog(_SessionNoPendingApi())
        try:
            while True:
                frames.append(next(gen))
        except StopIteration:
            pass
        self.assertIn('x-1', ''.join(frames))

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

    def test_session_list_endpoint_marks_pending_permission_without_workspace(self):
        live_session = MagicMock()
        live_session.is_alive = True
        live_session.is_working = False
        live_session.pending_control_request_tool.return_value = 'Bash'
        live_session.recent_events.return_value = [_FakeSessionEvent('control_request')]
        manager = _FakeManager(records=[
            _FakeRecord(
                task_id='PROJ-3',
                task_summary='approval',
                status='active',
                agent_session_id='s',
            ),
        ])
        manager.get_session = lambda task_id: live_session if task_id == 'PROJ-3' else None
        app = create_app(session_manager=manager)
        response = app.test_client().get('/api/sessions')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload[0]['has_pending_permission'])
        self.assertEqual(payload[0]['pending_permission_tool_name'], 'Bash')

    def test_drain_queued_task_comment_uses_agent_service(self):
        service = MagicMock()
        service.comment_runs.drain_next_queued_task_comment.return_value = {
            'ok': True, 'started': True, 'comment_id': 'c1',
        }

        started = _drain_queued_task_comment(service, 'PROJ-1')

        self.assertTrue(started)
        service.comment_runs.drain_next_queued_task_comment.assert_called_once_with('PROJ-1')

    def test_drain_queued_task_comment_handles_missing_service(self):
        self.assertFalse(_drain_queued_task_comment(None, 'PROJ-1'))

    def test_idle_event_stream_drains_queued_comment_before_idle(self):
        service = MagicMock()
        service.comment_runs.drain_next_queued_task_comment.return_value = {
            'ok': True, 'started': False, 'comment_id': 'c1',
        }

        frames = list(_event_stream_generator(
            self.manager, None, 'PROJ-1', service,
        ))

        self.assertTrue(any('session_idle' in frame for frame in frames))
        service.comment_runs.drain_next_queued_task_comment.assert_called_once_with('PROJ-1')

    def test_live_follow_drains_queue_after_result_event(self):
        session = MagicMock()
        session.is_alive = False
        session.events_after.side_effect = [
            ([_FakeSessionEvent('result')], 1),
            ([], 1),
        ]
        service = MagicMock()
        service.comment_runs.drain_next_queued_task_comment.return_value = {
            'ok': True, 'started': True, 'comment_id': 'c1',
        }

        frames = list(_follow_live_session(
            session, agent_service=service, task_id='PROJ-1',
        ))

        self.assertTrue(any('session_closed' in frame for frame in frames))
        service.comment_runs.drain_next_queued_task_comment.assert_called_once_with('PROJ-1')

    def test_result_event_completes_then_drains(self):
        event = SimpleNamespace(
            event_type='result',
            raw={'type': 'result', 'is_error': False, 'result': 'Done.'},
        )
        service = MagicMock()
        _advance_task_comments_after_result(event, service, 'PROJ-1')
        service.comment_runs.complete_in_progress_task_comments.assert_called_once_with(
            'PROJ-1',
            success=True,
            result_text='Done.',
            result_received_at_epoch=0.0,
        )
        service.comment_runs.drain_next_queued_task_comment.assert_called_once_with('PROJ-1')

    def test_errored_result_completes_with_success_false(self):
        event = SimpleNamespace(
            event_type='result', raw={'type': 'result', 'is_error': True},
        )
        service = MagicMock()
        _advance_task_comments_after_result(event, service, 'PROJ-1')
        service.comment_runs.complete_in_progress_task_comments.assert_called_once_with(
            'PROJ-1',
            success=False,
            result_text='',
            result_received_at_epoch=0.0,
        )

    def test_non_result_event_is_ignored(self):
        event = SimpleNamespace(event_type='assistant', raw={'type': 'assistant'})
        service = MagicMock()
        _advance_task_comments_after_result(event, service, 'PROJ-1')
        service.comment_runs.complete_in_progress_task_comments.assert_not_called()
        service.comment_runs.drain_next_queued_task_comment.assert_not_called()

    def test_complete_helper_tolerates_missing_method_and_errors(self):
        # Service without the method (older stub) → no raise.
        _complete_in_progress_task_comments(object(), 'PROJ-1', True)
        boom = MagicMock()
        boom.complete_in_progress_task_comments.side_effect = RuntimeError('x')
        _complete_in_progress_task_comments(boom, 'PROJ-1', True)  # swallowed

    def test_backlog_replay_never_completes_comments(self):
        # Replaying the backlog (browser reconnect / resumed-session
        # history) re-walks OLD result events. It must NOT drive comment
        # completion: doing so attributed a stale, unrelated answer to
        # whatever comment was IN_PROGRESS and flipped its badge to
        # ADDRESSED while Claude was still working. Completion is driven
        # only by LIVE results + the scan-loop fallback.
        service = MagicMock()
        session = MagicMock()
        session.recent_events.return_value = [
            _FakeSessionEvent('assistant'),
            _FakeSessionEvent('result'),
        ]
        frames = list(
            _replay_session_backlog(session, agent_service=service, task_id='T1'),
        )
        # The UI still gets every backlog event…
        self.assertEqual(len(frames), 2)
        # …but completion is never triggered from replayed events.
        service.comment_runs.complete_in_progress_task_comments.assert_not_called()
        service.comment_runs.drain_next_queued_task_comment.assert_not_called()


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
                agent_session_id='abc',
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
        # Every tree carries the change-colouring inputs the Files
        # tab needs (same shape the conflict markers use).
        for entry in payload['trees']:
            self.assertIsInstance(entry['conflicted_files'], list)
            self.assertIsInstance(entry['changed_files'], list)
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
            # Base resolves (not a local HEAD fallback) → real base reported.
            'kato_webserver.app.resolve_base_ref',
            return_value=('origin/master', False),
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

        # Mirror resolve_base_ref's real contract: a known base resolves (not a
        # local fallback); an empty base falls back to HEAD (is_local). On a
        # clone WITH an origin remote, that fallback is the "no base" error case.
        def _resolve_ref(cwd, base):  # noqa: ARG001
            return (f'origin/{base}', False) if base else ('HEAD', True)

        with patch(
            'kato_webserver.app.detect_default_branch',
            side_effect=_branch_for,
        ), patch(
            'kato_webserver.app.resolve_base_ref',
            side_effect=_resolve_ref,
        ), patch(
            'kato_webserver.app.has_origin_remote',
            return_value=True,
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
        # Error is now precise + actionable — names the repo and
        # tells the operator to set ``destination_branch`` in the
        # kato config rather than the vague "could not detect" we
        # used to emit (which sent operators down the wrong rabbit
        # hole, looking at git state instead of their config).
        self.assertIn("'backend'", backend_diff['error'])
        self.assertIn('destination_branch', backend_diff['error'])


class _RepoIdsRecord:
    def __init__(self, repository_ids=None):
        self.repository_ids = repository_ids or []


class _RepoIdsWorkspaceManager:
    def __init__(self, record=None, workspace_path=None):
        self._record = record
        self._workspace_path = workspace_path

    def get(self, task_id):  # noqa: ARG002
        return self._record

    def workspace_path(self, task_id):  # noqa: ARG002
        if self._workspace_path is None:
            raise ValueError('no workspace path')
        return self._workspace_path


class TaskRepositoryIdsTests(unittest.TestCase):
    def test_returns_metadata_list_when_no_extra_repos_on_disk(self) -> None:
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = pathlib.Path(tmp) / 'TASK-1'
            task_dir.mkdir()
            for repo in ('backend', 'client'):
                (task_dir / repo / '.git').mkdir(parents=True)
            manager = _RepoIdsWorkspaceManager(
                record=_RepoIdsRecord(repository_ids=['backend', 'client']),
                workspace_path=task_dir,
            )
            self.assertEqual(_task_repository_ids(manager, 'TASK-1'), ['backend', 'client'])

    def test_appends_disk_repo_not_in_metadata(self) -> None:
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = pathlib.Path(tmp) / 'TASK-1'
            task_dir.mkdir()
            for repo in ('backend', 'client', 'new-repo'):
                (task_dir / repo / '.git').mkdir(parents=True)
            manager = _RepoIdsWorkspaceManager(
                record=_RepoIdsRecord(repository_ids=['backend', 'client']),
                workspace_path=task_dir,
            )
            result = _task_repository_ids(manager, 'TASK-1')
            self.assertEqual(result[:2], ['backend', 'client'])
            self.assertIn('new-repo', result)

    def test_falls_back_to_disk_when_metadata_has_no_ids(self) -> None:
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = pathlib.Path(tmp) / 'TASK-1'
            task_dir.mkdir()
            (task_dir / 'backend' / '.git').mkdir(parents=True)
            manager = _RepoIdsWorkspaceManager(
                record=_RepoIdsRecord(repository_ids=[]),
                workspace_path=task_dir,
            )
            self.assertEqual(_task_repository_ids(manager, 'TASK-1'), ['backend'])

    def test_falls_back_to_disk_when_no_record(self) -> None:
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = pathlib.Path(tmp) / 'TASK-1'
            task_dir.mkdir()
            (task_dir / 'backend' / '.git').mkdir(parents=True)
            manager = _RepoIdsWorkspaceManager(record=None, workspace_path=task_dir)
            self.assertEqual(_task_repository_ids(manager, 'TASK-1'), ['backend'])

    def test_returns_empty_list_when_manager_is_none(self) -> None:
        self.assertEqual(_task_repository_ids(None, 'TASK-1'), [])


class ModelEndpointTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(session_manager=_FakeManager())
        self.client = self.app.test_client()

    def test_get_models_returns_list(self):
        response = self.client.get('/api/models')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIn('models', body)
        ids = [m['id'] for m in body['models']]
        # Stable CLI aliases (always resolve to the latest version), NOT a
        # hardcoded pinned id like 'claude-opus-4-7' that goes stale. These are
        # guaranteed by FALLBACK_MODELS even when live discovery is unavailable
        # (e.g. CI with no claude binary). Fable is intentionally NOT asserted
        # here: it has no CLI alias and is gated to appear ONLY when discovery
        # confirms it's available — see model_catalog (offering an unconfirmed
        # pinned model is what produced the "Fable 5 unavailable" error).
        self.assertIn('sonnet', ids)
        self.assertIn('opus', ids)
        defaults = [m['id'] for m in body['models'] if m.get('default')]
        self.assertEqual(defaults, ['sonnet'])

    def test_models_default_flag_follows_configured_runner_model(self):
        # When the chat runner is configured with a model (KATO_CLAUDE_MODEL),
        # /api/models must flag THAT model as default — that's the one spawn falls
        # back to when a task has no override, and the composer selects it.
        from claude_core_lib.claude_core_lib.helpers.model_catalog import reset_models_cache
        reset_models_cache()
        self.addCleanup(reset_models_cache)
        runner = SimpleNamespace(_defaults=SimpleNamespace(binary='claude', model='opus'))
        self.app.config['PLANNING_SESSION_RUNNER'] = runner
        body = self.client.get('/api/models').get_json()
        defaults = [m['id'] for m in body['models'] if m.get('default')]
        self.assertEqual(defaults, ['opus'])  # moved off sonnet onto the configured opus

    def test_models_default_unchanged_when_no_runner_model_configured(self):
        from claude_core_lib.claude_core_lib.helpers.model_catalog import reset_models_cache
        reset_models_cache()
        self.addCleanup(reset_models_cache)
        # No runner / empty model => CLI default kept (sonnet).
        self.app.config['PLANNING_SESSION_RUNNER'] = SimpleNamespace(
            _defaults=SimpleNamespace(binary='claude', model=''),
        )
        body = self.client.get('/api/models').get_json()
        self.assertEqual([m['id'] for m in body['models'] if m.get('default')], ['sonnet'])

    def test_unmatched_configured_model_is_surfaced_not_silently_misflagged(self):
        # When the configured model matches NO offered id, the picker must not
        # keep the stale discovery flag (sonnet) — that would claim a model
        # that will not run. The configured value itself is surfaced as the
        # flagged entry, because spawn passes it verbatim.
        from claude_core_lib.claude_core_lib.helpers.model_catalog import reset_models_cache
        reset_models_cache()
        self.addCleanup(reset_models_cache)
        runner = SimpleNamespace(
            _defaults=SimpleNamespace(binary='claude', model='some-custom-model'),
        )
        self.app.config['PLANNING_SESSION_RUNNER'] = runner
        body = self.client.get('/api/models').get_json()
        flagged = [m for m in body['models'] if m.get('default')]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]['id'], 'some-custom-model')
        self.assertIn('(configured)', flagged[0]['label'])
        # The catalog's own entries lost their stale flag.
        self.assertNotIn(
            'sonnet', [m['id'] for m in flagged],
        )

    def test_match_model_alias_handles_alias_full_id_and_miss(self):
        from kato_webserver.app import _match_model_alias
        ids = ['fable', 'opus', 'sonnet', 'haiku']
        self.assertEqual(_match_model_alias('opus', ids), 'opus')
        self.assertEqual(_match_model_alias('OPUS', ids), 'opus')
        self.assertEqual(_match_model_alias('claude-opus-4-8', ids), 'opus')  # full id → family
        self.assertEqual(_match_model_alias('gpt-5.5', ['gpt-5.5']), 'gpt-5.5')  # codex direct
        self.assertEqual(_match_model_alias('mystery-model', ids), '')  # no match
        # Fable is now a real CLI alias like the others ("Provide an alias for
        # the latest model (e.g. 'fable', 'opus', or 'sonnet')"), so it matches
        # by FAMILY — the alias genuinely runs the latest fable, whatever
        # concrete version was configured, so this can't misreport what spawns.
        self.assertEqual(_match_model_alias('fable', ids), 'fable')
        self.assertEqual(_match_model_alias('claude-fable-5', ids), 'fable')
        self.assertEqual(_match_model_alias('claude-fable-5[1m]', ids), 'fable')
        self.assertEqual(_match_model_alias('claude-fable-6', ids), 'fable')
        # A family the picker doesn't offer still matches nothing.
        self.assertEqual(_match_model_alias('claude-fable-5', ['opus']), '')

    def test_get_openrouter_models_returns_catalog(self):
        from unittest.mock import patch
        from kato_core_lib.helpers import openrouter_model_discovery as disc
        disc.reset_openrouter_models_cache()
        self.addCleanup(disc.reset_openrouter_models_cache)
        with patch.object(
            disc, 'discover_openrouter_models',
            return_value=[{'id': 'openrouter/openai/gpt-4o', 'label': 'OpenAI: GPT-4o'}],
        ):
            response = self.client.get('/api/openrouter/models')
        self.assertEqual(response.status_code, 200)
        models = response.get_json()['models']
        self.assertEqual(models[0]['id'], 'openrouter/openai/gpt-4o')

    def test_get_openrouter_models_survives_discovery_failure(self):
        from unittest.mock import patch
        from kato_core_lib.helpers import openrouter_model_discovery as disc
        with patch.object(disc, 'discover_openrouter_models', side_effect=RuntimeError('boom')):
            response = self.client.get('/api/openrouter/models')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['models'], [])

    def test_set_and_get_session_model(self):
        self.client.post('/api/sessions/PROJ-1/model', json={'model': 'opus'})
        response = self.client.get('/api/sessions/PROJ-1/model')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['model'], 'opus')

    def test_clear_session_model_by_posting_empty(self):
        self.client.post('/api/sessions/PROJ-1/model', json={'model': 'opus'})
        self.client.post('/api/sessions/PROJ-1/model', json={'model': ''})
        response = self.client.get('/api/sessions/PROJ-1/model')
        self.assertEqual(response.get_json()['model'], '')


class PromptDraftEndpointTests(unittest.TestCase):
    """Server-side composer draft (text + images) at .kato-prompts.json."""

    _IMG = {'media_type': 'image/png', 'data': 'AAAA'}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = Path(self._tmp.name)
        self.app = create_app(
            session_manager=_FakeManager(),
            workspace_manager=_RepoIdsWorkspaceManager(workspace_path=self.ws),
        )
        self.client = self.app.test_client()

    def test_get_empty_when_no_draft(self):
        response = self.client.get('/api/sessions/T1/draft')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'text': '', 'images': []})

    def test_post_then_get_round_trips_text_and_images(self):
        self.client.post(
            '/api/sessions/T1/draft', json={'text': 'fix it', 'images': [self._IMG]},
        )
        self.assertEqual(
            self.client.get('/api/sessions/T1/draft').get_json(),
            {'text': 'fix it', 'images': [self._IMG]},
        )

    def test_post_blank_clears_the_draft(self):
        self.client.post('/api/sessions/T1/draft', json={'text': 'hi', 'images': []})
        self.client.post('/api/sessions/T1/draft', json={'text': '', 'images': []})
        self.assertEqual(
            self.client.get('/api/sessions/T1/draft').get_json(),
            {'text': '', 'images': []},
        )

    def test_no_workspace_manager_get_empty_and_post_503(self):
        app = create_app(session_manager=_FakeManager())  # no workspace_manager
        client = app.test_client()
        self.assertEqual(
            client.get('/api/sessions/T1/draft').get_json(), {'text': '', 'images': []},
        )
        self.assertEqual(
            client.post('/api/sessions/T1/draft', json={'text': 'x'}).status_code, 503,
        )

    def test_unresolvable_workspace_is_a_safe_noop(self):
        app = create_app(
            session_manager=_FakeManager(),
            workspace_manager=_RepoIdsWorkspaceManager(workspace_path=None),
        )
        client = app.test_client()
        self.assertEqual(
            client.get('/api/sessions/T1/draft').get_json(), {'text': '', 'images': []},
        )
        self.assertEqual(
            client.post('/api/sessions/T1/draft', json={'text': 'x'}).status_code, 200,
        )


class ScanTriggerEndpointTests(unittest.TestCase):
    def test_trigger_sets_force_event_and_returns_triggered(self):
        import threading
        force_event = threading.Event()
        in_progress = threading.Event()
        app = create_app(
            session_manager=_FakeManager(),
            force_scan_event=force_event,
            scan_in_progress_event=in_progress,
        )
        response = app.test_client().post('/api/scan/trigger')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['status'], 'triggered')
        self.assertTrue(force_event.is_set())

    def test_trigger_returns_scanning_when_scan_in_progress(self):
        import threading
        force_event = threading.Event()
        in_progress = threading.Event()
        in_progress.set()
        app = create_app(
            session_manager=_FakeManager(),
            force_scan_event=force_event,
            scan_in_progress_event=in_progress,
        )
        response = app.test_client().post('/api/scan/trigger')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['status'], 'scanning')
        self.assertFalse(force_event.is_set())

    def test_trigger_returns_503_when_no_event_wired(self):
        app = create_app(session_manager=_FakeManager())
        response = app.test_client().post('/api/scan/trigger')
        self.assertEqual(response.status_code, 503)


class EffortRespawnDecisionTests(unittest.TestCase):
    """`_effort_change_needs_respawn`: only an idle, image-less session
    whose effort differs from an explicit override should respawn."""

    def _app(self, override=''):
        return SimpleNamespace(
            config={'TASK_EFFORT_OVERRIDES': ({'T1': override} if override else {})},
        )

    def _mgr(self, session):
        manager = MagicMock()
        manager.get_session.return_value = session
        return manager

    def _session(self, **kw):
        base = dict(is_alive=True, is_working=False, effort='low')
        base.update(kw)
        return SimpleNamespace(**base)

    def test_images_never_respawn(self):
        self.assertFalse(_effort_change_needs_respawn(
            self._app('high'), self._mgr(self._session()), 'T1', [{'data': 'x'}],
        ))

    def test_no_override_never_respawn(self):
        self.assertFalse(_effort_change_needs_respawn(
            self._app(''), self._mgr(self._session()), 'T1', [],
        ))

    def test_no_live_session_no_respawn(self):
        dead = SimpleNamespace(is_alive=False, is_working=False, effort='')
        self.assertFalse(_effort_change_needs_respawn(
            self._app('high'), self._mgr(dead), 'T1', [],
        ))

    def test_busy_session_not_interrupted(self):
        self.assertFalse(_effort_change_needs_respawn(
            self._app('high'), self._mgr(self._session(is_working=True)), 'T1', [],
        ))

    def test_same_effort_no_respawn(self):
        self.assertFalse(_effort_change_needs_respawn(
            self._app('high'), self._mgr(self._session(effort='high')), 'T1', [],
        ))

    def test_idle_different_effort_respawns(self):
        self.assertTrue(_effort_change_needs_respawn(
            self._app('high'), self._mgr(self._session(effort='low')), 'T1', [],
        ))


class ModelRespawnDecisionTests(unittest.TestCase):
    """``_model_change_needs_respawn``: only an idle, image-less session
    whose ``--model`` differs from an explicit operator override should
    respawn. Mirrors the effort decision exactly — the operator-reported
    bug was that an explicit model change ("I changed model to opus")
    was forwarded into a live session still spawned with the OLD model,
    and the CLI errored on every message because the old model was
    inaccessible.
    """

    def _app(self, override=''):
        return SimpleNamespace(
            config={'TASK_MODEL_OVERRIDES': ({'T1': override} if override else {})},
        )

    def _mgr(self, session):
        manager = MagicMock()
        manager.get_session.return_value = session
        return manager

    def _session(self, **kw):
        base = dict(is_alive=True, is_working=False, model='claude-fable-5')
        base.update(kw)
        return SimpleNamespace(**base)

    def test_images_never_respawn(self):
        from kato_webserver.app import _model_change_needs_respawn
        self.assertFalse(_model_change_needs_respawn(
            self._app('claude-opus-4-8'),
            self._mgr(self._session()), 'T1', [{'data': 'x'}],
        ))

    def test_no_override_never_respawn(self):
        from kato_webserver.app import _model_change_needs_respawn
        self.assertFalse(_model_change_needs_respawn(
            self._app(''), self._mgr(self._session()), 'T1', [],
        ))

    def test_no_live_session_no_respawn(self):
        from kato_webserver.app import _model_change_needs_respawn
        dead = SimpleNamespace(is_alive=False, is_working=False, model='')
        self.assertFalse(_model_change_needs_respawn(
            self._app('claude-opus-4-8'), self._mgr(dead), 'T1', [],
        ))

    def test_busy_session_not_interrupted(self):
        from kato_webserver.app import _model_change_needs_respawn
        self.assertFalse(_model_change_needs_respawn(
            self._app('claude-opus-4-8'),
            self._mgr(self._session(is_working=True)), 'T1', [],
        ))

    def test_same_model_no_respawn(self):
        from kato_webserver.app import _model_change_needs_respawn
        self.assertFalse(_model_change_needs_respawn(
            self._app('claude-opus-4-8'),
            self._mgr(self._session(model='claude-opus-4-8')), 'T1', [],
        ))

    def test_idle_different_model_respawns(self):
        from kato_webserver.app import _model_change_needs_respawn
        self.assertTrue(_model_change_needs_respawn(
            self._app('claude-opus-4-8'),
            self._mgr(self._session(model='claude-fable-5')), 'T1', [],
        ))


class LessonsTabsExcludedTests(unittest.TestCase):
    """kato's lessons-state dirs (``lessons/`` · ``lesson-candidates/``) live
    inside KATO_WORKSPACES_ROOT next to the task clones, so the workspace walk
    lists them — but they are NOT tasks and must never surface as planning-UI
    tabs. Lessons stay in files; they just get no tab."""

    def test_session_list_excludes_lessons_state_dirs(self):
        workspace_manager = _FakeWorkspaceManager(records=[
            _FakeWorkspaceRecord(
                task_id='UNA-2727', task_summary='real task',
                status='active', repository_ids=['repo'],
            ),
            _FakeWorkspaceRecord(
                task_id='lessons', status='errored', repository_ids=[],
            ),
            _FakeWorkspaceRecord(
                task_id='lesson-candidates', status='errored', repository_ids=[],
            ),
        ])
        app = create_app(
            session_manager=_FakeManager(records=[]),
            workspace_manager=workspace_manager,
        )
        response = app.test_client().get('/api/sessions')
        self.assertEqual(response.status_code, 200)
        task_ids = {entry['task_id'] for entry in response.get_json()}
        self.assertIn('UNA-2727', task_ids)          # real task kept
        self.assertNotIn('lessons', task_ids)         # phantom tab gone
        self.assertNotIn('lesson-candidates', task_ids)


class ReadOnlyRepoEndpointTests(unittest.TestCase):
    """Read-only repos: /files badges them; the re-check endpoint can clear them."""

    def setUp(self):
        import os
        from pathlib import Path
        from unittest.mock import patch
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        ctx = patch.dict(
            os.environ,
            {'KATO_READ_ONLY_REPOS_PATH': str(Path(self._tmp.name) / 'ro.json')},
        )
        ctx.start()
        self.addCleanup(ctx.stop)

    def test_files_endpoint_marks_read_only_repos(self):
        from pathlib import Path
        from kato_core_lib.helpers.read_only_repos_store import set_read_only_repos
        root = Path(self._tmp.name)
        repo_a = root / 'PROJ-1' / 'client'
        repo_b = root / 'PROJ-1' / 'ext-lib'
        for repo in (repo_a, repo_b):
            (repo / '.git').mkdir(parents=True)
        set_read_only_repos('PROJ-1', ['ext-lib'])
        workspace_manager = _FakeWorkspaceManager(
            records=[_FakeWorkspaceRecord(
                task_id='PROJ-1', repository_ids=['client', 'ext-lib'],
            )],
            repo_paths={
                ('PROJ-1', 'client'): str(repo_a),
                ('PROJ-1', 'ext-lib'): str(repo_b),
            },
        )
        app = create_app(
            session_manager=_FakeManager(records=[]),
            workspace_manager=workspace_manager,
        )
        response = app.test_client().get('/api/sessions/PROJ-1/files')
        self.assertEqual(response.status_code, 200)
        by_id = {t['repo_id']: t for t in response.get_json()['trees']}
        self.assertFalse(by_id['client']['read_only'])   # writable
        self.assertTrue(by_id['ext-lib']['read_only'])    # reference / no push

    def test_recheck_push_endpoint_reports_now_writable(self):
        agent_service = SimpleNamespace(
            recheck_repository_push_access=lambda task_id, repo_id: True,
        )
        app = create_app(
            session_manager=_FakeManager(records=[]), agent_service=agent_service,
        )
        response = app.test_client().post(
            '/api/sessions/PROJ-1/repositories/ext-lib/recheck-push',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'repo_id': 'ext-lib', 'read_only': False})

    def test_recheck_push_endpoint_503_without_agent_service(self):
        app = create_app(session_manager=_FakeManager(records=[]))
        response = app.test_client().post(
            '/api/sessions/PROJ-1/repositories/ext-lib/recheck-push',
        )
        self.assertEqual(response.status_code, 503)


class ChatEffortDefaultTests(unittest.TestCase):
    """Removing "Auto" means kato resolves + passes a CONCRETE effort.

    With no per-task override and no configured runner effort, the chat path
    must fall back to a concrete level (DEFAULT_CHAT_EFFORT) so the CLI never
    silently picks its own — the operator always knows the level that ran.
    """

    def test_default_is_concrete_not_empty(self):
        # The whole point: never "" (which would mean no --effort / "Auto").
        self.assertTrue(DEFAULT_CHAT_EFFORT)
        self.assertEqual(DEFAULT_CHAT_EFFORT, 'high')

    def test_configured_chat_effort_falls_back_to_concrete_default(self):
        # Runner with no effort configured (or no runner at all) → the
        # concrete default, never ''.
        app = SimpleNamespace(config={'PLANNING_SESSION_RUNNER': None})
        self.assertEqual(_configured_chat_effort(app), DEFAULT_CHAT_EFFORT)
        runner = SimpleNamespace(_defaults=SimpleNamespace(effort=''))
        app = SimpleNamespace(config={'PLANNING_SESSION_RUNNER': runner})
        self.assertEqual(_configured_chat_effort(app), DEFAULT_CHAT_EFFORT)

    def test_configured_chat_effort_respects_an_explicit_config(self):
        runner = SimpleNamespace(_defaults=SimpleNamespace(effort='max'))
        app = SimpleNamespace(config={'PLANNING_SESSION_RUNNER': runner})
        self.assertEqual(_configured_chat_effort(app), 'max')

    def test_chat_spawn_passes_concrete_effort_when_no_override(self):
        # End-to-end: a tab whose subprocess has exited respawns, and the
        # runner is handed an explicit effort (not '').
        import tempfile
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )

        class _RecordingRunner:
            def __init__(self):
                self.calls = []

            def resume_session_for_chat(self, **kwargs):
                self.calls.append(kwargs)

        with tempfile.TemporaryDirectory() as state_dir:
            manager = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=lambda **_: None,
            )
            manager.adopt_session_id('PROJ-1', agent_session_id='pinned-id')
            runner = _RecordingRunner()
            app = create_app(
                session_manager=manager,
                planning_session_runner=runner,
            )
            response = app.test_client().post(
                '/api/sessions/PROJ-1/messages',
                json={'text': 'continue'},
            )

        self.assertEqual(response.status_code, 200)
        # No override was set, so the spawn must still carry a concrete level.
        self.assertEqual(runner.calls[0]['effort'], DEFAULT_CHAT_EFFORT)



class TaskChatsCarryTheirBackendTests(unittest.TestCase):
    """Each chat says which CLI produced it — read from the RECORD.

    An operator who switches backends keeps their older conversations, and
    each one resumes through the CLI that wrote it. Labelling chats from the
    CURRENT setting would mislabel every one of them after a switch.
    """

    def _client(self, record):
        manager = MagicMock()
        manager.get_record.return_value = record
        app = create_app(session_manager=manager, agent_service=MagicMock())
        return app.test_client()

    def test_the_backend_is_reported_per_chat(self) -> None:
        record = SimpleNamespace(
            task_id='PROJ-1', agent_session_id='sid-1',
            previous_session_ids=[], agent_backend='codex',
        )
        body = self._client(record).get('/api/sessions/PROJ-1/chats').get_json()

        self.assertTrue(body['chats'])
        self.assertEqual(body['chats'][0]['agent_backend'], 'codex')

    def test_a_record_without_a_backend_reports_empty_not_a_guess(self) -> None:
        # Records written before kato tracked this exist on every operator's
        # disk; the UI shows no chip rather than inventing one.
        record = SimpleNamespace(
            task_id='PROJ-1', agent_session_id='sid-1', previous_session_ids=[],
        )
        body = self._client(record).get('/api/sessions/PROJ-1/chats').get_json()

        self.assertEqual(body['chats'][0]['agent_backend'], '')

if __name__ == '__main__':
    unittest.main()
