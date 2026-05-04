from __future__ import annotations

import io
import json
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from kato_core_lib.client.claude.streaming_session import SessionEvent, StreamingClaudeSession


class _FakeProc:
    """Minimal subprocess.Popen stand-in for the streaming session tests."""

    def __init__(self, stdout_lines: list[str] | None = None) -> None:
        self.pid = 1234
        self._stdout_buffer = b''.join(
            (line + '\n').encode('utf-8') for line in (stdout_lines or [])
        )
        self.stdout = io.BytesIO(self._stdout_buffer)
        self.stderr = io.BytesIO(b'')
        self.stdin = MagicMock()
        self.stdin.write = MagicMock()
        self.stdin.flush = MagicMock()
        self.stdin.close = MagicMock()
        self._returncode: int | None = None
        self._wait_event = threading.Event()
        self.signals_sent: list[int] = []
        self._exit_after_close = True

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        if self._returncode is not None:
            return self._returncode
        if self._exit_after_close:
            self._returncode = 0
            return 0
        if timeout is None:
            self._wait_event.wait()
            return self._returncode or 0
        if not self._wait_event.wait(timeout):
            import subprocess
            raise subprocess.TimeoutExpired(cmd=['claude'], timeout=timeout)
        return self._returncode or 0

    def send_signal(self, sig):
        self.signals_sent.append(sig)
        self._returncode = -sig

    def force_exit(self, returncode: int = 0) -> None:
        self._returncode = returncode
        self._wait_event.set()


class StreamingClaudeSessionTests(unittest.TestCase):
    def test_start_requires_task_id(self) -> None:
        with self.assertRaisesRegex(ValueError, 'task_id is required'):
            StreamingClaudeSession(task_id='')

    def test_start_launches_subprocess_and_pins_session_id(self) -> None:
        fake_proc = _FakeProc(stdout_lines=[
            json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 'live-123'}),
        ])
        with patch(
            'kato_core_lib.client.claude.streaming_session.subprocess.Popen',
            return_value=fake_proc,
        ) as mock_popen, patch(
            'kato_core_lib.client.claude.streaming_session.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-1', cwd='/tmp')
            session.start()

        cmd = mock_popen.call_args.args[0]
        self.assertIn('-p', cmd)
        self.assertIn('--output-format', cmd)
        self.assertIn('stream-json', cmd)
        self.assertIn('--input-format', cmd)
        # A session-id is pinned up front so a restart can resume it.
        self.assertIn('--session-id', cmd)
        # That pinned id should match what's exposed by the session.
        pinned = cmd[cmd.index('--session-id') + 1]
        self.assertEqual(session.claude_session_id, pinned)

    def test_start_with_resume_id_does_not_pin_a_new_session_id(self) -> None:
        fake_proc = _FakeProc()
        with patch(
            'kato_core_lib.client.claude.streaming_session.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'kato_core_lib.client.claude.streaming_session.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(
                task_id='PROJ-1',
                resume_session_id='earlier-session-uuid',
            )
            session.start()
        self.assertEqual(session.claude_session_id, '')

    def test_send_user_message_writes_ndjson_envelope(self) -> None:
        fake_proc = _FakeProc()
        fake_proc._exit_after_close = False
        with patch(
            'kato_core_lib.client.claude.streaming_session.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'kato_core_lib.client.claude.streaming_session.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-1')
            session.start()
            session.send_user_message('please add a hover state')

        fake_proc.stdin.write.assert_called_once()
        written_bytes = fake_proc.stdin.write.call_args.args[0]
        self.assertTrue(written_bytes.endswith(b'\n'))
        payload = json.loads(written_bytes.decode('utf-8').strip())
        self.assertEqual(payload['type'], 'user')
        self.assertEqual(
            payload['message']['content'][0]['text'],
            'please add a hover state',
        )
        # Cleanup: trigger graceful exit so the daemon threads stop.
        fake_proc.force_exit()
        session.terminate(grace_seconds=0.2)

    def test_send_permission_response_writes_control_response_envelope(self) -> None:
        fake_proc = _FakeProc()
        fake_proc._exit_after_close = False
        with patch(
            'kato_core_lib.client.claude.streaming_session.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'kato_core_lib.client.claude.streaming_session.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-1')
            session.start()
            # Stash a captured request so allow echoes the original input
            # back as ``updatedInput`` (the real wire contract for
            # ``--permission-prompt-tool stdio``).
            with session._pending_control_requests_lock:
                session._pending_control_requests['req-77'] = {
                    'tool_name': 'Bash',
                    'input': {'command': 'ls /tmp'},
                }
            session.send_permission_response('req-77', allow=True, rationale='ok')

        written = fake_proc.stdin.write.call_args.args[0]
        payload = json.loads(written.decode('utf-8').strip())
        self.assertEqual(payload['type'], 'control_response')
        response = payload['response']
        self.assertEqual(response['subtype'], 'success')
        self.assertEqual(response['request_id'], 'req-77')
        decision = response['response']
        self.assertEqual(decision['behavior'], 'allow')
        self.assertEqual(decision['updatedInput'], {'command': 'ls /tmp'})
        fake_proc.force_exit()
        session.terminate(grace_seconds=0.2)

    def test_send_permission_response_deny_carries_rationale(self) -> None:
        fake_proc = _FakeProc()
        fake_proc._exit_after_close = False
        with patch(
            'kato_core_lib.client.claude.streaming_session.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'kato_core_lib.client.claude.streaming_session.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-1')
            session.start()
            session.send_permission_response('req-99', allow=False, rationale='not safe')

        written = fake_proc.stdin.write.call_args.args[0]
        payload = json.loads(written.decode('utf-8').strip())
        decision = payload['response']['response']
        self.assertEqual(decision['behavior'], 'deny')
        self.assertEqual(decision['message'], 'not safe')
        fake_proc.force_exit()
        session.terminate(grace_seconds=0.2)

    def test_send_user_message_raises_when_subprocess_dead(self) -> None:
        session = StreamingClaudeSession(task_id='PROJ-1')
        with self.assertRaisesRegex(RuntimeError, 'subprocess is not running'):
            session.send_user_message('hi')

    def test_events_iter_yields_until_terminal(self) -> None:
        fake_proc = _FakeProc(stdout_lines=[
            json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 's1'}),
            json.dumps({'type': 'assistant', 'message': {'role': 'assistant'}}),
            json.dumps({'type': 'result', 'subtype': 'success',
                        'is_error': False, 'result': 'done'}),
        ])
        with patch(
            'kato_core_lib.client.claude.streaming_session.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'kato_core_lib.client.claude.streaming_session.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-1')
            session.start()
            collected: list[SessionEvent] = []
            # Wait briefly for reader thread to drain the stdout buffer.
            for _ in range(40):
                if len(session.recent_events()) >= 3:
                    break
                time.sleep(0.05)
            for event in session.events_iter():
                collected.append(event)
                if event.is_terminal:
                    break

        self.assertEqual([event.event_type for event in collected],
                         ['system', 'assistant', 'result'])
        self.assertTrue(collected[-1].is_terminal)
        self.assertIs(session.terminal_event, collected[-1])

    def test_terminate_closes_stdin_and_kills_after_grace(self) -> None:
        fake_proc = _FakeProc()
        fake_proc._exit_after_close = False  # simulate hung subprocess
        with patch(
            'kato_core_lib.client.claude.streaming_session.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'kato_core_lib.client.claude.streaming_session.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-1')
            session.start()
            session.terminate(grace_seconds=0.1)

        fake_proc.stdin.close.assert_called_once()
        # SIGTERM is the first escalation after the grace window.
        self.assertIn(15, fake_proc.signals_sent)


class StreamingClaudeSessionDockerModeTests(unittest.TestCase):
    """``KATO_CLAUDE_DOCKER`` plumbing for the streaming spawn path.

    Sandbox-wrap on streaming sessions now gates on the new
    ``docker_mode_on`` attribute, not on ``permission_mode ==
    bypassPermissions``. This separates *containment* (docker) from the
    *prompt layer* (bypass), so an operator can run docker=true with
    permission prompts on for the strongest combined posture.
    """

    def test_docker_mode_off_does_not_wrap_spawn_even_when_bypass_permissions(self) -> None:
        fake_proc = _FakeProc()
        with patch(
            'kato_core_lib.client.claude.streaming_session.subprocess.Popen',
            return_value=fake_proc,
        ) as mock_popen, patch(
            'kato_core_lib.client.claude.streaming_session.shutil.which',
            return_value='/usr/local/bin/claude',
        ), patch(
            'kato_core_lib.sandbox.manager.wrap_command',
        ) as mock_wrap:
            session = StreamingClaudeSession(
                task_id='PROJ-1',
                cwd='/tmp/repo',
                permission_mode='bypassPermissions',
                docker_mode_on=False,
            )
            session.start()

        mock_wrap.assert_not_called()
        spawn_argv = mock_popen.call_args.args[0]
        # Streaming session resolves the binary via shutil.which.
        self.assertNotEqual(spawn_argv[:2], ['docker', 'run'])

    def test_docker_mode_on_wraps_spawn_in_sandbox(self) -> None:
        fake_proc = _FakeProc()
        with patch(
            'kato_core_lib.client.claude.streaming_session.subprocess.Popen',
            return_value=fake_proc,
        ) as mock_popen, patch(
            'kato_core_lib.client.claude.streaming_session.shutil.which',
            return_value='/usr/local/bin/claude',
        ), patch(
            'kato_core_lib.sandbox.manager.ensure_image',
        ), patch(
            'kato_core_lib.sandbox.manager.check_spawn_rate',
        ), patch(
            'kato_core_lib.sandbox.manager.enforce_no_workspace_secrets',
        ), patch(
            'kato_core_lib.sandbox.manager.record_spawn',
        ) as mock_record, patch(
            'kato_core_lib.sandbox.manager.wrap_command',
            return_value=['docker', 'run', '--rm', 'kato-sandbox', 'claude'],
        ) as mock_wrap, patch(
            'kato_core_lib.sandbox.manager.make_container_name',
            return_value='kato-sandbox-PROJ-1-abcd1234',
        ):
            session = StreamingClaudeSession(
                task_id='PROJ-1',
                cwd='/tmp/repo',
                docker_mode_on=True,
            )
            session.start()

        mock_wrap.assert_called_once()
        wrap_kwargs = mock_wrap.call_args.kwargs
        self.assertEqual(wrap_kwargs['task_id'], 'PROJ-1')
        self.assertEqual(wrap_kwargs['workspace_path'], '/tmp/repo')
        # Audit log fires before the subprocess starts.
        mock_record.assert_called_once()
        spawn_argv = mock_popen.call_args.args[0]
        self.assertEqual(spawn_argv[:2], ['docker', 'run'])

    def test_docker_mode_default_is_off(self) -> None:
        session = StreamingClaudeSession(task_id='PROJ-1')
        self.assertFalse(session._docker_mode_on)

    def test_docker_mode_off_does_not_append_sandbox_addendum(self) -> None:
        from kato_core_lib.sandbox.system_prompt import (
            SANDBOX_SYSTEM_PROMPT_ADDENDUM,
            WORKSPACE_SCOPE_ADDENDUM,
        )

        session = StreamingClaudeSession(
            task_id='PROJ-1',
            docker_mode_on=False,
        )
        cmd = session._build_command()
        # Workspace addendum is always appended; sandbox is only added in docker mode.
        self.assertIn('--append-system-prompt', cmd)
        idx = cmd.index('--append-system-prompt')
        self.assertEqual(cmd[idx + 1], WORKSPACE_SCOPE_ADDENDUM)
        self.assertNotIn(SANDBOX_SYSTEM_PROMPT_ADDENDUM, cmd[idx + 1])

    def test_docker_mode_on_appends_sandbox_addendum(self) -> None:
        from kato_core_lib.sandbox.system_prompt import (
            SANDBOX_SYSTEM_PROMPT_ADDENDUM,
            WORKSPACE_SCOPE_ADDENDUM,
        )

        session = StreamingClaudeSession(
            task_id='PROJ-1',
            docker_mode_on=True,
        )
        cmd = session._build_command()
        self.assertIn('--append-system-prompt', cmd)
        idx = cmd.index('--append-system-prompt')
        self.assertEqual(
            cmd[idx + 1],
            f'{WORKSPACE_SCOPE_ADDENDUM}\n\n{SANDBOX_SYSTEM_PROMPT_ADDENDUM}',
        )


class StreamingClaudeSessionCredentialOutputScanTests(unittest.TestCase):
    """Output-side credential scan on the streaming terminal event.

    Closes residual #18 on the streaming spawn path. Mirrors the
    one-shot behavior in
    ``ClaudeCliClient._scan_response_for_credentials`` so both paths
    produce the same audit signal when the agent emits a credential
    pattern in its final response. Detective-only: the response has
    already crossed to Anthropic by the time we see it.
    """

    def test_warning_logged_when_terminal_event_contains_credential(self) -> None:
        fake_aws_key = 'AKIAEXAMPLEFAKE12345'
        terminal_line = json.dumps({
            'type': 'result',
            'subtype': 'success',
            'is_error': False,
            'result': f'Here is the value: {fake_aws_key}',
            'session_id': 'live-1',
        })
        fake_proc = _FakeProc(stdout_lines=[terminal_line])
        with patch(
            'kato_core_lib.client.claude.streaming_session.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'kato_core_lib.client.claude.streaming_session.shutil.which',
            return_value='/usr/local/bin/claude',
        ), self.assertLogs('kato.workflow.StreamingClaudeSession', level='WARNING') as cm:
            session = StreamingClaudeSession(task_id='PROJ-CRED')
            session.start()
            # Consume events to drive the reader thread to the terminal.
            for _ in session.events_iter():
                pass

        joined = ' '.join(cm.output)
        self.assertIn('aws_access_key_id', joined)
        self.assertIn('CREDENTIAL PATTERN DETECTED', joined)
        # Full credential value must never be logged.
        self.assertNotIn(fake_aws_key, joined)
        self.assertIn('REDACTED', joined)

    def test_no_warning_when_terminal_event_is_clean(self) -> None:
        terminal_line = json.dumps({
            'type': 'result',
            'subtype': 'success',
            'is_error': False,
            'result': 'Done — edits written.',
            'session_id': 'live-2',
        })
        fake_proc = _FakeProc(stdout_lines=[terminal_line])
        with patch(
            'kato_core_lib.client.claude.streaming_session.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'kato_core_lib.client.claude.streaming_session.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-CLEAN')
            with self.assertNoLogs('kato.workflow.StreamingClaudeSession', level='WARNING'):
                session.start()
                for _ in session.events_iter():
                    pass

    def test_warning_logged_when_terminal_event_contains_phishing_pattern(self) -> None:
        """Streaming-side detective scan also fires for phishing (#16).

        Mirrors test_warning_logged_when_response_contains_phishing_pattern
        in the one-shot test file. Without this assertion, a regression
        that drops the phishing-detector call from the streaming path
        leaves residual #16 silently undefended on the streaming spawn.
        """
        # Use a code-fenced sudo block — the sudo_command regex anchors
        # to start-of-line / special chars (not bare mid-prose) to keep
        # false positives on words like "pseudo" out. This is the exact
        # phishing shape the addendum tells the agent to NOT generate.
        terminal_line = json.dumps({
            'type': 'result',
            'subtype': 'success',
            'is_error': False,
            'result': 'On your host:\n```bash\nsudo apt install build-essential\n```',
            'session_id': 'live-phish',
        })
        fake_proc = _FakeProc(stdout_lines=[terminal_line])
        with patch(
            'kato_core_lib.client.claude.streaming_session.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'kato_core_lib.client.claude.streaming_session.shutil.which',
            return_value='/usr/local/bin/claude',
        ), self.assertLogs('kato.workflow.StreamingClaudeSession', level='WARNING') as cm:
            session = StreamingClaudeSession(task_id='PROJ-PHISH')
            session.start()
            for _ in session.events_iter():
                pass

        joined = ' '.join(cm.output)
        # Distinct PHISHING tag, separate from CREDENTIAL.
        self.assertIn('PHISHING PATTERN DETECTED', joined)
        # Pattern name names the shape so operator can review the
        # specific suggestion in the planning UI.
        self.assertIn('sudo_command', joined)
        self.assertIn('residual #16', joined)


if __name__ == '__main__':
    unittest.main()
