"""A Codex chat: one process per turn, one continuous event log.

Driven by a REAL subprocess — a tiny python script standing in for the codex
binary, emitting the JSONL shapes a real ``codex exec --json`` run produces.
Mocking the subprocess would test the mock: the whole point of this class is
that it stitches several short-lived processes into something that behaves
like one conversation, and that only shows up when processes actually start
and exit.
"""

from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from codex_core_lib.codex_core_lib.session.streaming import (
    CODEX_EVENT_TURN_ABORTED,
    CODEX_EVENT_TURN_FAILED,
    SessionEvent,
    StreamingCodexSession,
)

_SETTLE_SECONDS = 5.0


def _fake_cli(script_body: str) -> Path:
    """Write a stand-in ``codex`` that emits the given JSONL."""
    tmp = Path(tempfile.mkdtemp()) / 'fake_codex.py'
    tmp.write_text(textwrap.dedent(script_body), encoding='utf-8')
    return tmp


def _wait_until(predicate, timeout: float = _SETTLE_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class _SessionHarness(unittest.TestCase):
    def _session(self, script_body: str, **kwargs) -> StreamingCodexSession:
        script = _fake_cli(script_body)
        calls = self.calls = []

        def build_command(*, prompt, resume_id):
            calls.append({'prompt': prompt, 'resume_id': resume_id})
            return [sys.executable, str(script), resume_id or '']

        session = StreamingCodexSession(
            task_id='PROJ-1', cwd=str(script.parent),
            build_command=build_command, **kwargs,
        )
        self.addCleanup(session.terminate)
        return session


_ONE_TURN = '''
    import json, sys
    resume = sys.argv[1] if len(sys.argv) > 1 else ''
    sys.stdin.read()
    print(json.dumps({"type": "thread.started", "thread_id": resume or "thread-abc"}))
    print(json.dumps({"type": "turn.started"}))
    print(json.dumps({"type": "item.completed",
                      "item": {"type": "agent_message", "text": "done"}}))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 12}}))
'''


class TurnLifecycleTests(_SessionHarness):
    def test_a_turn_streams_its_events_and_ends_terminal(self) -> None:
        session = self._session(_ONE_TURN)
        session.send_user_message('hello')

        self.assertTrue(_wait_until(lambda: session.terminal_event is not None))
        types = [e.event_type for e in session.recent_events()]
        # The OPERATOR'S PROMPT leads the log. The CLI takes it on stdin and
        # never echoes it, so without this the transcript held answers to a
        # question that vanished on the next page reload.
        self.assertEqual(types[0], 'user')
        self.assertEqual(types[1], 'thread.started')
        self.assertIn('item.completed', types)
        self.assertEqual(types[-1], 'turn.completed')
        self.assertTrue(session.terminal_event.is_terminal)

    def test_the_chat_stays_alive_between_turns(self) -> None:
        # THE difference from a persistent-process transport: no process runs
        # between turns, and the chat is still perfectly usable. Reporting
        # liveness from the process would tell every caller it had died.
        session = self._session(_ONE_TURN)
        session.send_user_message('first')
        self.assertTrue(_wait_until(lambda: not session.is_working))

        self.assertTrue(session.is_alive)
        self.assertFalse(session.is_working)

    def test_the_first_turn_learns_the_thread_id_and_later_turns_resume_it(self) -> None:
        session = self._session(_ONE_TURN)
        session.send_user_message('first')
        self.assertTrue(_wait_until(lambda: session.agent_session_id == 'thread-abc'))

        session.send_user_message('second')
        self.assertTrue(_wait_until(lambda: len(self.calls) == 2))

        self.assertEqual(self.calls[0]['resume_id'], '')
        self.assertEqual(self.calls[1]['resume_id'], 'thread-abc',
                         'the second turn did not resume the conversation')

    def test_learning_the_id_notifies_the_host_so_it_can_be_persisted(self) -> None:
        # Without this the id lives only in memory and the chat cannot be
        # resumed after a restart.
        session = self._session(_ONE_TURN)
        seen = []
        session._session_id_correction_callback = seen.append
        session.send_user_message('hello')

        self.assertTrue(_wait_until(lambda: seen == ['thread-abc']))

    def test_a_resumed_chat_reuses_the_id_it_was_given(self) -> None:
        session = self._session(_ONE_TURN, agent_session_id='thread-existing')
        session.send_user_message('back again')

        self.assertTrue(_wait_until(lambda: len(self.calls) == 1))
        self.assertEqual(self.calls[0]['resume_id'], 'thread-existing')


class QueuedMessageTests(_SessionHarness):
    _SLOW_TURN = '''
        import json, sys, time
        resume = sys.argv[1] if len(sys.argv) > 1 else ''
        sys.stdin.read()
        print(json.dumps({"type": "thread.started", "thread_id": resume or "thread-abc"}), flush=True)
        time.sleep(0.4)
        print(json.dumps({"type": "turn.completed"}), flush=True)
    '''

    def test_a_message_sent_mid_turn_is_queued_not_dropped(self) -> None:
        # There is no stdin to interrupt a running turn, so the alternative
        # to queueing is losing what the operator typed.
        session = self._session(self._SLOW_TURN)
        session.send_user_message('first')
        self.assertTrue(_wait_until(lambda: session.is_working))
        session.send_user_message('while busy')

        self.assertTrue(_wait_until(lambda: len(self.calls) == 2, timeout=8))
        self.assertIn('while busy', self.calls[1]['prompt'])

    def test_several_queued_messages_ride_one_follow_up_turn(self) -> None:
        session = self._session(self._SLOW_TURN)
        session.send_user_message('first')
        self.assertTrue(_wait_until(lambda: session.is_working))
        session.send_user_message('second')
        session.send_user_message('third')

        self.assertTrue(_wait_until(lambda: len(self.calls) == 2, timeout=8))
        self.assertIn('second', self.calls[1]['prompt'])
        self.assertIn('third', self.calls[1]['prompt'])


class FailureTests(_SessionHarness):
    def test_a_turn_that_crashes_still_produces_a_terminal_event(self) -> None:
        # Otherwise the UI's in-flight indicator spins forever on a crash.
        session = self._session('''
            import sys
            sys.stdin.read()
            sys.stderr.write("boom\\n")
            sys.exit(3)
        ''')
        session.send_user_message('hello')

        self.assertTrue(_wait_until(lambda: session.terminal_event is not None))
        self.assertEqual(session.terminal_event.event_type, CODEX_EVENT_TURN_ABORTED)
        self.assertEqual(session.terminal_event.raw.get('returncode'), 3)

    def test_stderr_is_captured_for_the_operator(self) -> None:
        session = self._session('''
            import sys
            sys.stdin.read()
            sys.stderr.write("not logged in\\n")
            sys.exit(1)
        ''')
        session.send_user_message('hello')

        self.assertTrue(_wait_until(
            lambda: any('not logged in' in l for l in session.stderr_snapshot())))

    def test_a_non_json_stdout_line_is_not_treated_as_an_event(self) -> None:
        # A CLI banner is not a protocol error.
        session = self._session('''
            import json, sys
            sys.stdin.read()
            print("codex-cli 0.132.0")
            print(json.dumps({"type": "turn.completed"}))
        ''')
        session.send_user_message('hello')

        self.assertTrue(_wait_until(lambda: session.terminal_event is not None))
        # The prompt, then the one event the CLI actually emitted — the
        # non-JSON line is still not treated as an event.
        self.assertEqual([e.event_type for e in session.recent_events()],
                         ['user', 'turn.completed'])

    def test_a_missing_binary_is_reported_not_raised(self) -> None:
        session = StreamingCodexSession(
            task_id='PROJ-1',
            build_command=lambda **kw: ['/nonexistent/codex-binary'],
        )
        self.addCleanup(session.terminate)
        session.send_user_message('hello')

        self.assertEqual(session.terminal_event.event_type, CODEX_EVENT_TURN_ABORTED)
        self.assertTrue(session.stderr_snapshot())

    def test_a_resumed_turn_reporting_a_different_id_does_not_overwrite(self) -> None:
        # That means the resume silently started a NEW conversation; keeping
        # the original id makes the divergence visible instead of hiding it.
        session = self._session('''
            import json, sys
            sys.stdin.read()
            print(json.dumps({"type": "thread.started", "thread_id": "thread-DIFFERENT"}))
            print(json.dumps({"type": "turn.completed"}))
        ''', agent_session_id='thread-original')
        session.send_user_message('hello')

        self.assertTrue(_wait_until(lambda: session.terminal_event is not None))
        self.assertEqual(session.agent_session_id, 'thread-original')


class EventAccessTests(_SessionHarness):
    def test_events_after_returns_only_the_new_slice(self) -> None:
        session = self._session(_ONE_TURN)
        session.send_user_message('hello')
        self.assertTrue(_wait_until(lambda: session.terminal_event is not None))

        first, mark = session.events_after(0)
        self.assertTrue(first)
        self.assertEqual(session.events_after(mark), ([], mark))

    def test_recent_events_honours_a_limit(self) -> None:
        session = self._session(_ONE_TURN)
        session.send_user_message('hello')
        self.assertTrue(_wait_until(lambda: session.terminal_event is not None))

        self.assertEqual(len(session.recent_events(limit=2)), 2)

    def test_an_event_serialises_for_the_stream(self) -> None:
        session = self._session(_ONE_TURN)
        session.send_user_message('hello')
        self.assertTrue(_wait_until(lambda: session.terminal_event is not None))

        payload = session.recent_events()[0].to_dict()
        self.assertIn('raw', payload)
        self.assertIn('received_at_epoch', payload)
        json.dumps(payload)


class TerminationTests(_SessionHarness):
    def test_terminate_ends_the_chat_and_refuses_further_messages(self) -> None:
        session = self._session(_ONE_TURN)
        session.terminate()

        self.assertFalse(session.is_alive)
        with self.assertRaises(RuntimeError):
            session.send_user_message('too late')

    def test_terminate_is_safe_to_call_twice(self) -> None:
        session = self._session(_ONE_TURN)
        session.terminate()
        session.terminate()

    def test_terminate_kills_an_in_flight_turn(self) -> None:
        session = self._session('''
            import sys, time
            sys.stdin.read()
            time.sleep(30)
        ''')
        session.send_user_message('hello')
        self.assertTrue(_wait_until(lambda: session.is_working))

        started = time.monotonic()
        session.terminate(grace_seconds=0.2)

        self.assertLess(time.monotonic() - started, 5,
                        'terminate waited on an unresponsive turn')
        self.assertFalse(session.is_alive)

    def test_an_empty_message_starts_nothing(self) -> None:
        session = self._session(_ONE_TURN)
        session.send_user_message('   ')

        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()


class TurnFailureIsExplainedTests(unittest.TestCase):
    """A dead turn must carry WHY, not just that it died.

    Reported as an unexplained ``turn.aborted`` bubble in the chat. Two
    faults met: ``turn.failed`` — the CLI's own terminal event, which names
    the refusal — was not in the terminal set, so it fell through to the
    synthesised abort; and that abort carried only a return code.

    The real case: a model picker still holding a Claude alias makes the CLI
    answer "The 'opus' model is not supported when using Codex with a ChatGPT
    account" and exit. That sentence is the entire fix instruction, and it
    was being discarded.
    """

    def test_turn_failed_is_terminal(self) -> None:
        event = SessionEvent(
            raw={'type': CODEX_EVENT_TURN_FAILED, 'error': {'message': 'x'}},
        )
        self.assertTrue(event.is_terminal)
        self.assertEqual(CODEX_EVENT_TURN_FAILED, 'turn.failed')

    def test_turn_completed_and_aborted_are_still_terminal(self) -> None:
        for kind in ('turn.completed', 'turn.aborted'):
            self.assertTrue(SessionEvent(raw={'type': kind}).is_terminal, kind)

    def test_an_ordinary_event_is_not_terminal(self) -> None:
        self.assertFalse(SessionEvent(raw={'type': 'item.completed'}).is_terminal)

    def _session(self):
        return StreamingCodexSession(task_id='T1', binary='codex')

    def test_the_reason_prefers_the_clis_own_error_event(self) -> None:
        session = self._session()
        session._append_event({
            'type': 'error',
            'message': "The 'opus' model is not supported when using Codex "
                       'with a ChatGPT account.',
        })
        reason = session._failure_reason(1, ['Reading prompt from stdin...'])
        self.assertIn("'opus' model is not supported", reason)

    def test_the_reason_falls_back_to_stderr(self) -> None:
        session = self._session()
        reason = session._failure_reason(1, ['something broke badly'])
        self.assertEqual(reason, 'something broke badly')

    def test_the_stdin_banner_is_not_mistaken_for_a_reason(self) -> None:
        # The CLI prints it on every run; reporting it as the failure would
        # be worse than saying nothing.
        session = self._session()
        reason = session._failure_reason(3, ['Reading prompt from stdin...'])
        self.assertIn('exited with code 3', reason)

    def test_a_bare_exit_still_yields_something_actionable(self) -> None:
        session = self._session()
        reason = session._failure_reason(137, [])
        self.assertIn('137', reason)
        self.assertIn('codex', reason)

    def test_the_latest_error_event_wins(self) -> None:
        session = self._session()
        session._append_event({'type': 'error', 'message': 'first'})
        session._append_event({'type': 'error', 'message': 'second'})
        self.assertEqual(session._failure_reason(1, []), 'second')


class TooOldCliIsRefusedUpFrontTests(unittest.TestCase):
    """A CLI without ``exec --json`` must fail the PROBE, not the turn.

    Reported as a chat that died on ``error: unknown option '--json'`` after
    the operator had already typed a message. That is the pre-Rust Codex CLI:
    it has no ``--json``, so every streamed turn is impossible on it. The
    check belongs at connection-validation time, where the setup panel can
    show what to install.

    Feature-detected, not version-compared — the flag is what is actually
    depended on.
    """

    def _client(self, help_output: str, help_returncode: int = 0):
        from unittest.mock import patch
        from codex_core_lib.codex_core_lib.cli_client import CodexCliClient
        client = CodexCliClient(binary='codex')
        calls = {'n': 0}

        def fake_run(argv, **kwargs):
            calls['n'] += 1
            if '--version' in argv:
                return SimpleNamespace(returncode=0, stdout='codex-cli 1.2.3', stderr='')
            return SimpleNamespace(
                returncode=help_returncode, stdout=help_output, stderr='',
            )

        return client, fake_run, patch

    def test_a_cli_without_json_is_refused(self) -> None:
        client, fake_run, patch = self._client('Usage: codex exec [options]\n  --quiet\n')
        with patch('codex_core_lib.codex_core_lib.cli_client.shutil.which',
                   return_value='/usr/local/bin/codex'), \
             patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   side_effect=fake_run):
            with self.assertRaises(RuntimeError) as caught:
                client.validate_connection()
        message = str(caught.exception)
        self.assertIn('too old', message)
        self.assertIn('npm install -g @openai/codex@latest', message)
        # Naming the resolved path matters: "unknown option" alone never said
        # WHICH codex ran, and several can be installed at once.
        self.assertIn('/usr/local/bin/codex', message)

    def test_a_modern_cli_passes(self) -> None:
        client, fake_run, patch = self._client(
            'Usage: codex exec [OPTIONS]\n      --json\n      --add-dir <DIR>\n',
        )
        with patch('codex_core_lib.codex_core_lib.cli_client.shutil.which',
                   return_value='/usr/local/bin/codex'), \
             patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   side_effect=fake_run):
            client.validate_connection()  # must not raise

    def test_an_unrunnable_help_probe_is_not_treated_as_failure(self) -> None:
        # A sandbox that blocks the probe must not make a working CLI look
        # broken — the turn itself would still succeed.
        client, fake_run, patch = self._client('', help_returncode=127)
        with patch('codex_core_lib.codex_core_lib.cli_client.shutil.which',
                   return_value='/usr/local/bin/codex'), \
             patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   side_effect=fake_run):
            client.validate_connection()  # must not raise


class PromptSurvivesAReloadTests(unittest.TestCase):
    """The operator's prompt must be in the EVENT LOG, not just the UI.

    Reported as "after reload of page i don't see the codex last prompt i
    sent him". ``codex exec`` takes the prompt on stdin and never echoes it,
    so the log held only the agent's output; the prompt existed solely as a
    local bubble the UI appends on send, which a reload discards. The
    operator came back to answers with no questions above them.
    """

    def _session(self, script: str) -> StreamingCodexSession:
        return StreamingCodexSession(task_id='T1', binary=str(_fake_cli(script)))

    def test_the_prompt_is_recorded_before_the_turn_runs(self) -> None:
        session = self._session(_ONE_TURN)
        self.addCleanup(session.terminate, 0.2)
        session.send_user_message('review my changes')

        self.assertTrue(_wait_until(lambda: session.terminal_event is not None))
        first = session.recent_events()[0]
        self.assertEqual(first.event_type, 'user')
        self.assertEqual(
            first.raw['message']['content'][0]['text'], 'review my changes',
        )

    def test_it_survives_the_replay_the_stream_sends_on_reconnect(self) -> None:
        # A page reload re-reads ``recent_events`` from index 0 — exactly
        # what the SSE endpoint replays on connect.
        session = self._session(_ONE_TURN)
        self.addCleanup(session.terminate, 0.2)
        session.send_user_message('hello')
        self.assertTrue(_wait_until(lambda: session.terminal_event is not None))

        replayed, _total = session.events_after(0)
        texts = [
            e.raw.get('message', {}).get('content', [{}])[0].get('text')
            for e in replayed if e.event_type == 'user'
        ]
        self.assertEqual(texts, ['hello'])

    def test_the_shape_matches_the_other_transport(self) -> None:
        # One wire shape for "the operator said this" is what lets a single
        # UI render either backend without a Codex-specific branch.
        session = self._session(_ONE_TURN)
        self.addCleanup(session.terminate, 0.2)
        session.send_user_message('hi')
        self.assertTrue(_wait_until(lambda: session.terminal_event is not None))

        raw = session.recent_events()[0].raw
        self.assertEqual(raw['type'], 'user')
        self.assertEqual(raw['message']['content'][0]['type'], 'text')

    def test_an_empty_message_records_nothing(self) -> None:
        session = self._session(_ONE_TURN)
        self.addCleanup(session.terminate, 0.2)
        session.send_user_message('   ')
        self.assertEqual(session.recent_events(), [])

    def test_a_queued_mid_turn_message_is_recorded_too(self) -> None:
        # Messages typed while a turn is running are joined into one
        # follow-up turn; that turn's prompt must be logged like any other.
        session = self._session(_ONE_TURN)
        self.addCleanup(session.terminate, 0.2)
        session.send_user_message('first')
        self.assertTrue(_wait_until(lambda: session.terminal_event is not None))
        session.send_user_message('second')
        self.assertTrue(_wait_until(
            lambda: sum(
                1 for e in session.recent_events() if e.event_type == 'user'
            ) == 2,
        ))
        texts = [
            e.raw['message']['content'][0]['text']
            for e in session.recent_events() if e.event_type == 'user'
        ]
        self.assertEqual(texts, ['first', 'second'])
