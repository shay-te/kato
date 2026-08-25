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

from codex_core_lib.codex_core_lib.session.streaming import (
    CODEX_EVENT_TURN_ABORTED,
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
        self.assertEqual(types[0], 'thread.started')
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
        self.assertEqual([e.event_type for e in session.recent_events()],
                         ['turn.completed'])

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
