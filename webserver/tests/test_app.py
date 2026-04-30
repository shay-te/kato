import unittest
from unittest.mock import MagicMock

from kato_webserver.app import create_app


class _FakeRecord:
    def __init__(self, **kwargs):
        self._payload = kwargs

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
        self.assertIn(b'PROJ-1', response.data)
        self.assertIn(b'do the thing', response.data)

    def test_index_renders_empty_state_when_no_sessions(self):
        empty_app = create_app(session_manager=_FakeManager(records=[]))
        client = empty_app.test_client()
        response = client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'kato:wait-planning', response.data)

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


if __name__ == '__main__':
    unittest.main()
