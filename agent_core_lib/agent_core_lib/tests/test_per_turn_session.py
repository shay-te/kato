"""The shared per-turn chat session, driven against real stand-in CLI processes.

A stand-in CLI is a tiny Python script run with ``sys.executable``: it prints
the JSON lines a real per-turn CLI would, so every test exercises the real
spawn, stdout reader, stderr drain and synthesised terminal events.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_core_lib.agent_core_lib.helpers.command_floor import GIT_ACCESS_LOCAL
from agent_core_lib.agent_core_lib.session.per_turn_session import PerTurnCliSession
from agent_core_lib.agent_core_lib.session.turn_events import (
    TURN_EVENT_TURN_ABORTED,
    TURN_EVENT_USER,
    agent_message,
    session_started,
    turn_completed,
)

_SETTLE = 5.0


def _wait(predicate, timeout: float = _SETTLE) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _script(body: str) -> Path:
    path = Path(tempfile.mkdtemp()) / 'fake_cli.py'
    path.write_text(textwrap.dedent(body), encoding='utf-8')
    return path


class _StdinCli(PerTurnCliSession):
    """A CLI that takes the prompt on stdin and speaks a vocabulary of its own."""

    DISPLAY_NAME = 'Fake'
    DEFAULT_BINARY = 'fake'
    PROMPT_ON_STDIN = True
    STDERR_NOISE = ('banner noise',)

    def _default_command(self, *, prompt: str, resume_id: str) -> list[str]:
        return ['fake', resume_id]

    def _translate_event(self, raw):
        kind = raw.get('kind')
        if kind == 'open':
            return [session_started(raw['id'])]
        if kind == 'say':
            return [agent_message(raw['text']), turn_completed()]
        if kind == 'raw':
            return [raw['event']]
        return []


class _ArgvCli(_StdinCli):
    PROMPT_ON_STDIN = False


_SAYS = '''
    import json, sys
    resume = sys.argv[1] if len(sys.argv) > 1 else ''
    prompt = sys.stdin.read()
    print(json.dumps({"kind": "open", "id": resume or "sess-1"}))
    print("a banner line that is not json")
    print(json.dumps({"kind": "ignored"}))
    print(json.dumps([1, 2]))
    print(json.dumps({"kind": "say", "text": "got:" + prompt.strip()[-20:]}))
'''


class _Harness(unittest.TestCase):
    def _session(self, body: str, cls=_StdinCli, **kwargs):
        script = _script(body)
        self.calls = []

        def build_command(*, prompt, resume_id):
            self.calls.append({'prompt': prompt, 'resume_id': resume_id})
            return [sys.executable, str(script), resume_id or '', prompt]

        session = cls(task_id='T-1', cwd=str(script.parent), build_command=build_command, **kwargs)
        self.addCleanup(session.terminate)
        return session

    def _replies(self, session) -> list[str]:
        return [
            event.raw['item']['text'] for event in session.recent_events()
            if event.event_type == 'item.completed'
        ]


class TurnTests(_Harness):
    def test_a_turn_is_translated_logged_and_ends_terminal(self) -> None:
        session = self._session(_SAYS)
        session.send_user_message('hello')
        self.assertTrue(_wait(lambda: session.terminal_event is not None))
        self.assertEqual(session.recent_events()[0].event_type, TURN_EVENT_USER)
        self.assertEqual(self._replies(session), ['got:hello'])
        self.assertEqual(session.agent_session_id, 'sess-1')
        self.assertIn('a banner line that is not json', session.stderr_snapshot())

    def test_the_next_turn_resumes_the_learned_id(self) -> None:
        session = self._session(_SAYS)
        session.send_user_message('one')
        self.assertTrue(_wait(lambda: not session.is_working and session.terminal_event))
        session.send_user_message('two')
        self.assertTrue(_wait(lambda: len(self._replies(session)) == 2))
        self.assertEqual([call['resume_id'] for call in self.calls], ['', 'sess-1'])

    def test_a_prompt_in_argv_closes_stdin(self) -> None:
        session = self._session(_SAYS, cls=_ArgvCli)
        session.send_user_message('via argv')
        self.assertTrue(_wait(lambda: session.terminal_event is not None))
        # The script read an EMPTY stdin: the prompt rode in argv only.
        self.assertEqual(self._replies(session), ['got:'])
        self.assertEqual(self.calls[0]['prompt'], 'via argv')

    def test_messages_sent_mid_turn_are_joined_into_one_follow_up(self) -> None:
        session = self._session('''
            import json, sys, time
            sys.stdin.read()
            time.sleep(0.4)
            print(json.dumps({"kind": "say", "text": "x"}))
        ''')
        session.send_user_message('first')
        session.send_user_message('second')
        session.send_user_message('third')
        self.assertTrue(_wait(lambda: len(self.calls) == 2 and not session.is_working))
        self.assertTrue(self.calls[1]['prompt'].endswith('second\n\nthird'))

    def test_an_empty_message_does_nothing(self) -> None:
        session = self._session(_SAYS)
        session.send_user_message('   ')
        self.assertEqual(self.calls, [])

    def test_a_terminated_chat_refuses_messages(self) -> None:
        session = self._session(_SAYS)
        session.terminate()
        session.terminate()
        self.assertFalse(session.is_alive)
        with self.assertRaises(RuntimeError):
            session.send_user_message('late')

    def test_start_sends_the_opener_and_revives(self) -> None:
        session = self._session(_SAYS)
        session.start()
        self.assertEqual(self.calls, [])
        session.start(initial_prompt='opener')
        self.assertTrue(_wait(lambda: session.terminal_event is not None))
        self.assertTrue(session.is_alive)


class FailureTests(_Harness):
    def test_a_process_without_a_terminal_event_is_aborted_with_a_reason(self) -> None:
        session = self._session('''
            import sys
            sys.stdin.read()
            sys.stderr.write("banner noise\\n")
            sys.stderr.write("the real reason\\n")
            sys.exit(3)
        ''')
        session.send_user_message('go')
        self.assertTrue(_wait(lambda: session.terminal_event is not None))
        aborted = session.terminal_event.raw
        self.assertEqual(aborted['type'], TURN_EVENT_TURN_ABORTED)
        self.assertEqual(aborted['returncode'], 3)
        self.assertEqual(aborted['error'], 'the real reason')

    def test_the_cli_s_own_error_event_beats_stderr(self) -> None:
        session = self._session('''
            import json, sys
            sys.stdin.read()
            print(json.dumps({"kind": "raw", "event": {"type": "error", "message": "model refused"}}))
            sys.exit(1)
        ''')
        session.send_user_message('go')
        self.assertTrue(_wait(lambda: session.terminal_event is not None))
        self.assertEqual(session.terminal_event.raw['error'], 'model refused')

    def test_with_nothing_to_go_on_the_exit_code_is_the_reason(self) -> None:
        session = self._session('''
            import sys
            sys.stdin.read()
            sys.exit(2)
        ''')
        session.send_user_message('go')
        self.assertTrue(_wait(lambda: session.terminal_event is not None))
        self.assertIn('exited with code 2', session.terminal_event.raw['error'])

    def test_a_binary_that_cannot_launch_aborts_the_turn(self) -> None:
        session = _StdinCli(
            task_id='T-1', build_command=lambda **_kw: ['/no/such/binary-anywhere'],
        )
        self.addCleanup(session.terminate)
        session.send_user_message('go')
        self.assertEqual(session.terminal_event.event_type, TURN_EVENT_TURN_ABORTED)
        self.assertIn('failed to launch', session.stderr_snapshot()[0])

    def test_terminate_kills_a_running_turn(self) -> None:
        session = self._session('''
            import sys, time
            sys.stdin.read()
            time.sleep(30)
        ''')
        session.send_user_message('go')
        self.assertTrue(_wait(lambda: session.is_working))
        session.terminate(grace_seconds=0.2)
        self.assertFalse(session.is_working)


class IdentityTests(_Harness):
    def test_a_different_id_on_resume_is_not_adopted(self) -> None:
        logger = MagicMock()
        session = self._session(_SAYS, agent_session_id='original', logger=logger)
        session._append_event(session_started('something-else'))
        self.assertEqual(session.agent_session_id, 'original')
        logger.warning.assert_called_once()

    def test_the_first_id_reaches_the_correction_callback(self) -> None:
        session = self._session(_SAYS)
        seen = []
        session._session_id_correction_callback = seen.append
        session._append_event(session_started('fresh-id'))
        session._append_event(session_started('fresh-id'))
        self.assertEqual(seen, ['fresh-id'])

    def test_a_failing_callback_is_logged_not_raised(self) -> None:
        logger = MagicMock()
        session = self._session(_SAYS, logger=logger)
        session._session_id_correction_callback = MagicMock(side_effect=RuntimeError('x'))
        session._append_event(session_started('fresh-id'))
        logger.exception.assert_called_once()

    def test_accessors(self) -> None:
        session = self._session(_SAYS, additional_dirs=('a', '', 'b'))
        self.assertEqual(session.task_id, 'T-1')
        self.assertTrue(session.cwd)
        self.assertEqual(session.allowed_additional_dirs(), ['a', 'b'])
        self.assertIsNone(session.poll_event(timeout=0))
        session._append_event({'type': 'turn.started'})
        self.assertEqual(session.poll_event(timeout=0).event_type, 'turn.started')
        self.assertEqual(session.events_after(-3)[1], 1)
        self.assertEqual(session.events_after(5), ([], 1))
        self.assertEqual(len(session.recent_events(limit=0)), 0)


class GitAccessTests(_Harness):
    def test_the_rules_travel_once_per_level_change(self) -> None:
        level = {'value': GIT_ACCESS_LOCAL}
        session = self._session(_SAYS, git_access_source=lambda: level['value'])
        self.assertEqual(session.git_access, GIT_ACCESS_LOCAL)
        session.send_user_message('one')
        self.assertTrue(_wait(lambda: not session.is_working and session.terminal_event))
        session.send_user_message('two')
        self.assertTrue(_wait(lambda: len(self.calls) == 2 and not session.is_working))
        self.assertNotEqual(self.calls[0]['prompt'], 'one')
        self.assertEqual(self.calls[1]['prompt'], 'two')

    def test_an_unreadable_setting_means_strict(self) -> None:
        def broken():
            raise RuntimeError('settings unreadable')
        strict = self._session(_SAYS).git_access
        self.assertEqual(self._session(_SAYS, git_access_source=broken).git_access, strict)


class _CleanExitCli(_StdinCli):
    CLEAN_EXIT_ENDS_TURN = True


class _FakeProc:
    """A process stand-in for the paths a real child cannot be made to take."""

    def __init__(self, *, stdin=None, stdout=(), wait_result=0, poll_result=None):
        self.stdin = stdin
        self.stdout = list(stdout)
        self.stderr = None
        self._wait_result = wait_result
        self._poll_result = poll_result
        self.killed = False

    def poll(self):
        return self._poll_result

    def wait(self, timeout=None):
        if isinstance(self._wait_result, BaseException):
            raise self._wait_result
        return self._wait_result

    def terminate(self):
        return None

    def kill(self):
        self.killed = True


class ProcessEdgeTests(_Harness):
    def test_a_clean_exit_completes_a_turn_when_the_cli_has_no_done_event(self) -> None:
        session = self._session('''
            import json, sys
            sys.stdin.read()
            print(json.dumps({"kind": "open", "id": "s"}))
        ''', cls=_CleanExitCli)
        session.send_user_message('go')
        self.assertTrue(_wait(lambda: session.terminal_event is not None))
        self.assertEqual(session.terminal_event.event_type, 'turn.completed')

    def test_while_working_the_running_turn_s_level_is_reported(self) -> None:
        session = self._session(_SAYS)
        session._proc = _FakeProc()
        session._turn_git_access = GIT_ACCESS_LOCAL
        self.assertEqual(session.git_access, GIT_ACCESS_LOCAL)
        session._proc = None

    def test_terminate_kills_a_child_that_will_not_exit_and_shrugs_off_os_errors(self) -> None:
        import subprocess as sp
        session = self._session(_SAYS)
        stubborn = _FakeProc(wait_result=sp.TimeoutExpired('x', 1))
        session._proc = stubborn
        session.terminate(grace_seconds=0)
        self.assertTrue(stubborn.killed)
        broken = _FakeProc()
        broken.terminate = MagicMock(side_effect=OSError('gone'))
        session._proc = broken
        session.terminate()

    def test_a_stdin_that_breaks_is_recorded_not_raised(self) -> None:
        session = self._session(_SAYS)
        pipe = MagicMock()
        pipe.write.side_effect = BrokenPipeError('closed')
        session._run_turn(_FakeProc(stdin=pipe, wait_result=1), 'hello')
        self.assertTrue(any('stdin write failed' in line for line in session.stderr_snapshot()))
        self.assertEqual(session.terminal_event.event_type, TURN_EVENT_TURN_ABORTED)

    def test_the_failure_reason_skips_empty_errors_and_non_dict_events(self) -> None:
        session = self._session(_SAYS)
        session._append_event({'type': 'error', 'message': '   '})
        session._events.append(type('E', (), {'raw': 'not a dict'})())
        self.assertEqual(session._failure_reason(4, ['', 'banner noise']), 'the fake process exited with code 4')

    def test_blank_lines_and_long_logs_are_bounded(self) -> None:
        from agent_core_lib.agent_core_lib.session import per_turn_session as module
        session = self._session(_SAYS)
        self.assertEqual(session._append_json_line('   '), [])
        with patch.object(module, '_MAX_RECENT_EVENTS', 3), patch.object(module, '_MAX_STDERR_LINES', 2):
            for _ in range(5):
                session._append_event({'type': 'turn.started'})
                session._record_stderr('line')
        session._record_stderr('   ')
        self.assertEqual(len(session.recent_events()), 3)
        self.assertEqual(len(session.stderr_snapshot()), 2)
        session._drain_stderr(_FakeProc())
        no_stdout = _FakeProc(wait_result=0)
        no_stdout.stdout = None
        session._run_turn(no_stdout, 'quiet')
        self.assertEqual(session.terminal_event.event_type, TURN_EVENT_TURN_ABORTED)


class HookTests(unittest.TestCase):
    def test_every_hook_is_abstract(self) -> None:
        base = PerTurnCliSession(task_id='T')
        with self.assertRaises(NotImplementedError):
            base._default_command(prompt='p', resume_id='')
        with self.assertRaises(NotImplementedError):
            base._translate_event({})
        with self.assertRaises(NotImplementedError):
            base.send_user_message('anything')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
