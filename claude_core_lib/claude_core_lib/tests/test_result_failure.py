"""A failed turn says why.

The chat showed "(result: error) failed" with no reason. The CLI had given one,
in a field nothing read, or on stderr, which reached only the log.
"""

from __future__ import annotations

import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from claude_core_lib.claude_core_lib.helpers.result_failure import failure_reason
from claude_core_lib.claude_core_lib.session.streaming import (
    SessionEvent,
    StreamingClaudeSession,
)

# The event the real CLI sent for a turn that failed during execution.
FAILED_DURING_EXECUTION = {
    'type': 'result',
    'subtype': 'error_during_execution',
    'is_error': True,
    'errors': ['No conversation found with session ID: 00000000-0000-4000-8000-000000000000'],
}


class FailureReasonTests(unittest.TestCase):
    def test_the_reason_in_result(self) -> None:
        self.assertEqual(failure_reason({'result': ' rate  limited '}), 'rate limited')

    def test_the_reason_in_errors_when_there_is_no_result(self) -> None:
        self.assertEqual(
            failure_reason(FAILED_DURING_EXECUTION),
            'No conversation found with session ID: 00000000-0000-4000-8000-000000000000',
        )

    def test_several_errors_are_joined_and_blanks_dropped(self) -> None:
        self.assertEqual(failure_reason({'errors': ['one', ' ', None, 'two']}), 'one; two')

    def test_result_wins_over_errors(self) -> None:
        self.assertEqual(failure_reason({'result': 'first', 'errors': ['second']}), 'first')

    def test_no_reason_at_all(self) -> None:
        for raw in ({}, {'result': ''}, {'result': None}, {'errors': []}, {'errors': 'oops'}):
            with self.subTest(raw=raw):
                self.assertEqual(failure_reason(raw), '')


class FailureDetailTests(unittest.TestCase):
    def _session(self, *stderr_lines: str) -> StreamingClaudeSession:
        session = StreamingClaudeSession(task_id='T-1', cwd='/tmp/repo')
        session.logger = MagicMock()
        session._stderr_lines = list(stderr_lines)
        return session

    def test_a_failed_turn_with_no_reason_carries_the_last_stderr_lines(self) -> None:
        session = self._session('one', 'two', 'three', 'API Error: 529')
        event = SessionEvent(raw={'type': 'result', 'is_error': True, 'errors': []})

        session._attach_failure_detail(event)

        self.assertEqual(event.raw['failure_detail'], 'two\nthree\nAPI Error: 529')

    def test_it_ships_with_the_event_rather_than_after_it(self) -> None:
        session = self._session('API Error: 529')
        line = json.dumps({'type': 'result', 'is_error': True}).encode() + b'\n'
        session._proc = SimpleNamespace(stdout=io.BytesIO(line))

        session._stdout_reader_loop()

        self.assertEqual(session._terminal_event.raw['failure_detail'], 'API Error: 529')

    def test_very_long_stderr_keeps_its_end(self) -> None:
        session = self._session('x' * 5000 + ' the reason')
        event = SessionEvent(raw={'type': 'result', 'is_error': True})

        session._attach_failure_detail(event)

        self.assertEqual(len(event.raw['failure_detail']), 600)
        self.assertTrue(event.raw['failure_detail'].endswith('the reason'))

    def test_a_turn_that_names_its_reason_needs_no_stderr(self) -> None:
        session = self._session('noise')
        event = SessionEvent(raw=dict(FAILED_DURING_EXECUTION))

        session._attach_failure_detail(event)

        self.assertNotIn('failure_detail', event.raw)

    def test_a_turn_that_worked_gets_nothing(self) -> None:
        session = self._session('a harmless warning')
        event = SessionEvent(raw={'type': 'result', 'is_error': False, 'result': ''})

        session._attach_failure_detail(event)

        self.assertNotIn('failure_detail', event.raw)

    def test_no_stderr_adds_nothing(self) -> None:
        session = self._session()
        event = SessionEvent(raw={'type': 'result', 'is_error': True})

        session._attach_failure_detail(event)

        self.assertNotIn('failure_detail', event.raw)

    def test_the_log_gives_the_reason_too(self) -> None:
        session = self._session()

        session._log_event_for_operator(SessionEvent(raw=dict(FAILED_DURING_EXECUTION)))

        message, *values = session.logger.info.call_args.args
        self.assertIn('No conversation found', message % tuple(values))


if __name__ == '__main__':
    unittest.main()
