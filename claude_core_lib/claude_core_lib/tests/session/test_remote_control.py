"""Remote Control over the host → CLI control channel.

The toggle is not a spawn flag (see ``helpers/remote_control.py``): the host
writes a ``control_request`` on the LIVE subprocess's stdin and the CLI
answers on stdout, where the session's own reader thread is the only reader.
These tests drive that round trip through a fake subprocess whose stdout is a
real pipe, so the reader thread does exactly what it does in production.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import unittest
from unittest.mock import MagicMock, patch

from claude_core_lib.claude_core_lib.helpers.remote_control import (
    REMOTE_CONTROL_OFF,
    remote_control_state,
    reset_remote_control_support_cache,
    supports_remote_control,
)
from claude_core_lib.claude_core_lib.session.streaming import StreamingClaudeSession

_ENABLE_BODY = {
    'session_url': 'https://claude.ai/code/session/abc',
    'connect_url': 'https://claude.ai/code/connect/env-1',
    'environment_id': 'env-1',
    'bridge_epoch': 3,
    'bridge_session_id': 'bridge-abc',
}


class _RespondingProc:
    """Fake CLI that answers control requests on a real stdout pipe.

    ``stdout`` must be a genuine blocking stream: the session reads it from
    its own thread with ``iter(readline, b'')`` while the caller blocks in
    ``set_remote_control``. A ``BytesIO`` of canned lines (what the other
    streaming tests use) cannot express that ordering.
    """

    def __init__(self, responder) -> None:
        self.pid = 4321
        read_fd, self._write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, 'rb', buffering=0)
        self.stderr = os.fdopen(os.pipe()[0], 'rb', buffering=0)
        self.stdin = MagicMock()
        self.stdin.write = self._on_write
        self.stdin.flush = MagicMock()
        self.stdin.close = MagicMock()
        self._responder = responder
        self._returncode: int | None = None
        self.written: list[dict] = []
        self._lock = threading.Lock()

    def _on_write(self, payload: bytes) -> None:
        envelope = json.loads(payload.decode('utf-8'))
        self.written.append(envelope)
        reply = self._responder(envelope)
        if reply is not None:
            self.emit(reply)

    def emit(self, event: dict) -> None:
        with self._lock:
            os.write(self._write_fd, (json.dumps(event) + '\n').encode('utf-8'))

    def close_stdout(self) -> None:
        with self._lock:
            os.close(self._write_fd)

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        self._returncode = 0
        return 0

    def send_signal(self, sig):
        self._returncode = -sig


def _success(request_id: str, body: dict | None = None) -> dict:
    response = {'subtype': 'success', 'request_id': request_id}
    if body is not None:
        response['response'] = body
    return {'type': 'control_response', 'response': response}


def _error(request_id: str, message: str) -> dict:
    return {
        'type': 'control_response',
        'response': {
            'subtype': 'error', 'request_id': request_id, 'error': message,
        },
    }


class RemoteControlStateTests(unittest.TestCase):
    def test_enable_body_becomes_state(self) -> None:
        self.assertEqual(remote_control_state(_ENABLE_BODY), {
            'enabled': True,
            'session_url': 'https://claude.ai/code/session/abc',
            'connect_url': 'https://claude.ai/code/connect/env-1',
            'bridge_session_id': 'bridge-abc',
        })

    def test_missing_body_is_off_not_none(self) -> None:
        # The disable reply carries no body; callers (and the JSON that
        # reaches a UI) must still see every key.
        self.assertEqual(remote_control_state(None), REMOTE_CONTROL_OFF)
        self.assertEqual(remote_control_state('nonsense'), REMOTE_CONTROL_OFF)

    def test_partial_body_fills_blanks(self) -> None:
        state = remote_control_state({'session_url': 'https://x/y'})
        self.assertTrue(state['enabled'])
        self.assertEqual(state['connect_url'], '')
        self.assertEqual(state['bridge_session_id'], '')


class SupportsRemoteControlTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_remote_control_support_cache()
        self.addCleanup(reset_remote_control_support_cache)

    def _run(self, stdout='', side_effect=None):
        if side_effect is not None:
            return patch.object(subprocess, 'run', side_effect=side_effect)
        return patch.object(
            subprocess, 'run',
            return_value=type('R', (), {
                'stdout': stdout, 'stderr': '', 'returncode': 0,
            })(),
        )

    def test_detected_from_help(self) -> None:
        with self._run(stdout='  --remote-control [name]  Start with Remote Control\n'):
            self.assertTrue(supports_remote_control('claude'))

    def test_absent_from_help(self) -> None:
        with self._run(stdout='  --model <model>  The model\n'):
            self.assertFalse(supports_remote_control('claude'))

    def test_unprobeable_binary_is_unsupported(self) -> None:
        # Conservative: hide the toggle rather than offer a switch that
        # silently does nothing.
        with self._run(side_effect=FileNotFoundError('no claude')):
            self.assertFalse(supports_remote_control('claude'))

    def test_cached_per_binary(self) -> None:
        with self._run(stdout='--remote-control [name]\n') as run:
            supports_remote_control('claude')
            supports_remote_control('claude')
            self.assertEqual(run.call_count, 1)


class SetRemoteControlTests(unittest.TestCase):
    def _session(self, responder) -> tuple[StreamingClaudeSession, _RespondingProc]:
        proc = _RespondingProc(responder)
        patcher = patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=proc,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        which = patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        )
        which.start()
        self.addCleanup(which.stop)
        session = StreamingClaudeSession(task_id='PROJ-1', cwd='/tmp')
        session.start()
        return session, proc

    def test_enable_returns_the_session_url(self) -> None:
        session, proc = self._session(
            lambda env: _success(env['request_id'], _ENABLE_BODY),
        )
        state = session.set_remote_control(True, name='host PROJ-1', timeout=5)
        self.assertTrue(state['enabled'])
        self.assertEqual(state['session_url'], _ENABLE_BODY['session_url'])
        self.assertEqual(session.remote_control['bridge_session_id'], 'bridge-abc')

        sent = proc.written[-1]
        self.assertEqual(sent['type'], 'control_request')
        self.assertEqual(sent['request'], {
            'subtype': 'remote_control',
            'enabled': True,
            'name': 'host PROJ-1',
        })
        self.assertTrue(sent['request_id'])

    def test_blank_name_is_omitted_not_sent_empty(self) -> None:
        # The CLI names the session after the machine when no name is given;
        # sending an empty string instead would name it "".
        session, proc = self._session(
            lambda env: _success(env['request_id'], _ENABLE_BODY),
        )
        session.set_remote_control(True, name='   ', timeout=5)
        self.assertNotIn('name', proc.written[-1]['request'])

    def test_disable_clears_the_state(self) -> None:
        session, _proc = self._session(
            lambda env: _success(
                env['request_id'],
                _ENABLE_BODY if env['request']['enabled'] else None,
            ),
        )
        session.set_remote_control(True, timeout=5)
        state = session.set_remote_control(False, timeout=5)
        self.assertEqual(state, REMOTE_CONTROL_OFF)
        self.assertEqual(session.remote_control, REMOTE_CONTROL_OFF)

    def test_cli_error_is_raised_with_its_message(self) -> None:
        session, _proc = self._session(
            lambda env: _error(env['request_id'], 'Remote Control cannot be enabled'),
        )
        with self.assertRaisesRegex(RuntimeError, 'cannot be enabled'):
            session.set_remote_control(True, timeout=5)
        self.assertEqual(session.remote_control, REMOTE_CONTROL_OFF)

    def test_response_is_not_replayed_into_the_chat_log(self) -> None:
        # It is plumbing for a blocked caller, not a turn — replaying it
        # would put a raw control envelope in the operator's transcript.
        session, _proc = self._session(
            lambda env: _success(env['request_id'], _ENABLE_BODY),
        )
        session.set_remote_control(True, timeout=5)
        types = [event.event_type for event in session.recent_events()]
        self.assertNotIn('control_response', types)

    def test_foreign_control_response_still_reaches_the_log(self) -> None:
        # Only OUR request ids are swallowed; anything else is a normal event.
        session, proc = self._session(lambda env: None)
        proc.emit(_success('someone-elses-id', {'ok': True}))
        deadline = threading.Event()
        for _ in range(50):
            if any(e.event_type == 'control_response' for e in session.recent_events()):
                break
            deadline.wait(0.02)
        self.assertIn(
            'control_response',
            [event.event_type for event in session.recent_events()],
        )

    def test_timeout_when_the_cli_never_answers(self) -> None:
        session, _proc = self._session(lambda env: None)
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            session.set_remote_control(True, timeout=1)

    def test_a_timed_out_enable_is_undone(self) -> None:
        # The CLI registers the bridge with the service BEFORE it can reply,
        # so "no answer in time" does not mean "did not happen". Without the
        # follow-up disable, a reply that lands after the timeout leaves the
        # session genuinely exposed while every layer above has been told the
        # toggle failed — the one failure direction this feature must not have.
        session, proc = self._session(lambda env: None)
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            session.set_remote_control(True, timeout=1)

        for _ in range(100):
            requests = [
                envelope['request'] for envelope in list(proc.written)
                if envelope.get('request', {}).get('subtype') == 'remote_control'
            ]
            if any(req['enabled'] is False for req in requests):
                break
            threading.Event().wait(0.02)
        self.assertEqual(
            [req['enabled'] for req in requests], [True, False],
            'a failed enable must be followed by a disable',
        )

    def test_a_refused_enable_is_undone_too(self) -> None:
        # Same reasoning: an error reply is the CLI's answer to THIS request,
        # not proof that no bridge exists (it may be reporting a later stage
        # of enabling). Converging costs one no-op request.
        session, proc = self._session(
            lambda env: (
                _error(env['request_id'], 'nope')
                if env['request']['enabled'] else _success(env['request_id'])
            ),
        )
        with self.assertRaises(RuntimeError):
            session.set_remote_control(True, timeout=5)

        for _ in range(100):
            sent = [
                envelope['request']['enabled'] for envelope in list(proc.written)
                if envelope.get('request', {}).get('subtype') == 'remote_control'
            ]
            if False in sent:
                break
            threading.Event().wait(0.02)
        self.assertIn(False, sent)

    def test_a_failed_disable_is_not_re_sent(self) -> None:
        # Only an ENABLE needs undoing. Retrying a failed disable in a loop
        # would be a request storm against a CLI that just said no.
        session, proc = self._session(
            lambda env: _error(env['request_id'], 'nope'),
        )
        with self.assertRaises(RuntimeError):
            session.set_remote_control(False, timeout=5)
        threading.Event().wait(0.2)
        self.assertEqual(len(proc.written), 1)

    def test_dead_session_refuses_up_front(self) -> None:
        session = StreamingClaudeSession(task_id='PROJ-1', cwd='/tmp')
        with self.assertRaisesRegex(RuntimeError, 'no live claude session'):
            session.set_remote_control(True)
        self.assertEqual(session.remote_control, REMOTE_CONTROL_OFF)

    def test_terminate_releases_a_blocked_caller(self) -> None:
        # Otherwise a Stop mid-toggle leaves the HTTP request that asked for
        # it parked for the whole timeout on a process that is already gone.
        session, _proc = self._session(lambda env: None)
        failures: list[str] = []

        def toggle() -> None:
            try:
                session.set_remote_control(True, timeout=30)
            except RuntimeError as exc:
                failures.append(str(exc))

        worker = threading.Thread(target=toggle)
        worker.start()
        threading.Event().wait(0.2)
        session.terminate(grace_seconds=0.1)
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(failures)
        self.assertIn('stopped', failures[0])

    def test_dead_session_reports_off_even_after_enabling(self) -> None:
        session, proc = self._session(
            lambda env: _success(env['request_id'], _ENABLE_BODY),
        )
        session.set_remote_control(True, timeout=5)
        proc.close_stdout()
        session.terminate(grace_seconds=0.1)
        self.assertEqual(session.remote_control, REMOTE_CONTROL_OFF)


if __name__ == '__main__':
    unittest.main()
