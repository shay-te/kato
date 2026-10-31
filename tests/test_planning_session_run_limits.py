"""How long kato waits for a run it started.

Driven by a simulated clock, so a three-hour run takes a millisecond. The shape
of each test is what the lid-closed case depends on: real work is never given
up on, however long it takes, and waiting for the operator costs nothing.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_core_lib.data_layers.service.planning_session_runner import (
    PlanningSessionRunner,
    StreamingSessionDefaults,
    _is_waiting_for_operator,
)

MINUTE = 60.0
HOUR = 60 * MINUTE
IDLE_LIMIT = 30 * MINUTE
RUN_LIMIT = 8 * HOUR
_NOT_TERMINAL = SimpleNamespace(is_terminal=False, raw={})
_TERMINAL = SimpleNamespace(is_terminal=True, raw={'result': 'done'})


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Session:
    """Each poll moves the clock on by ``step`` and asks ``script`` what happens."""

    def __init__(self, clock: _Clock, step: float, script) -> None:
        self._clock = clock
        self._step = step
        self._script = script
        self.is_alive = True
        self.is_working = False
        self.waiting_tool = ''

    def pending_control_request_tool(self) -> str:
        return self.waiting_tool

    def poll_event(self, timeout: float = 0.0):
        self._clock.now += self._step
        return self._script(self, self._clock.now - 1000.0)


class RunLimitTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.runner = PlanningSessionRunner(
            session_manager=MagicMock(),
            defaults=StreamingSessionDefaults(),
            max_wait_seconds=IDLE_LIMIT,
            max_run_seconds=RUN_LIMIT,
            clock=self.clock,
        )
        self.runner.logger = MagicMock()

    def _wait(self, step, script):
        return self.runner._wait_for_terminal_event(
            _Session(self.clock, step, script), task_id='T',
        )

    def test_three_hours_of_real_work_is_not_given_up_on(self):
        def working(_session, elapsed):
            return _TERMINAL if elapsed >= 3 * HOUR else _NOT_TERMINAL

        self.assertIs(self._wait(10 * MINUTE, working), _TERMINAL)
        self.runner.logger.warning.assert_not_called()

    def test_a_long_quiet_stretch_under_the_limit_is_fine(self):
        def quiet_then_done(_session, elapsed):
            if elapsed >= 90 * MINUTE:
                return _TERMINAL
            return _NOT_TERMINAL if int(elapsed) % int(25 * MINUTE) == 0 else None

        self.assertIs(self._wait(5 * MINUTE, quiet_then_done), _TERMINAL)

    def test_a_silent_run_is_given_up_on_at_the_idle_limit(self):
        self.assertIsNone(self._wait(MINUTE, lambda *_: None))
        self.assertEqual(self.clock.now - 1000.0, IDLE_LIMIT)
        self.assertIn('produced nothing', self.runner.logger.warning.call_args.args[0])

    def test_a_run_that_goes_silent_later_still_stops(self):
        def stops_talking(_session, elapsed):
            return _NOT_TERMINAL if elapsed <= HOUR else None

        self.assertIsNone(self._wait(MINUTE, stops_talking))
        self.assertEqual(self.clock.now - 1000.0, HOUR + IDLE_LIMIT)

    def test_a_run_that_talks_forever_stops_at_the_cap(self):
        def looping(_session, elapsed):
            return _NOT_TERMINAL if int(elapsed) % int(10 * MINUTE) == 0 else None

        self.assertIsNone(self._wait(MINUTE, looping))
        self.assertEqual(self.clock.now - 1000.0, RUN_LIMIT)
        self.assertIn('ran past', self.runner.logger.warning.call_args.args[0])

    def test_output_on_every_single_poll_still_stops_at_the_cap(self):
        self.assertIsNone(self._wait(MINUTE, lambda *_: _NOT_TERMINAL))
        self.assertEqual(self.clock.now - 1000.0, RUN_LIMIT)

    def test_a_night_waiting_for_approval_costs_nothing(self):
        def waits_then_finishes(session, elapsed):
            session.waiting_tool = 'Bash' if elapsed < 10 * HOUR else ''
            return _TERMINAL if elapsed >= 10 * HOUR + MINUTE else None

        self.assertIs(self._wait(MINUTE, waits_then_finishes), _TERMINAL)
        self.runner.logger.warning.assert_not_called()

    def test_the_idle_clock_runs_again_once_the_operator_answers(self):
        def answered_then_silent(session, elapsed):
            session.waiting_tool = 'Bash' if elapsed < 5 * HOUR else ''
            return None

        self.assertIsNone(self._wait(MINUTE, answered_then_silent))
        self.assertLess(self.clock.now - 1000.0, 5 * HOUR + IDLE_LIMIT + 2 * MINUTE)
        self.assertGreater(self.clock.now - 1000.0, 5 * HOUR + IDLE_LIMIT - 2 * MINUTE)

    def test_waiting_does_not_count_towards_the_cap_either(self):
        def looping_after_a_long_wait(session, elapsed):
            session.waiting_tool = 'Bash' if elapsed < 6 * HOUR else ''
            return _NOT_TERMINAL if int(elapsed) % int(10 * MINUTE) == 0 else None

        self.assertIsNone(self._wait(MINUTE, looping_after_a_long_wait))
        self.assertGreater(self.clock.now - 1000.0, RUN_LIMIT + 5 * HOUR)

    def test_a_session_that_dies_ends_the_wait_even_while_waiting(self):
        def dies(session, elapsed):
            session.waiting_tool = 'Bash'
            session.is_alive = elapsed < HOUR
            session.terminal_event = None
            return None

        self.assertIsNone(self._wait(MINUTE, dies))
        self.assertEqual(self.clock.now - 1000.0, HOUR)

    def test_a_cap_below_the_idle_limit_does_not_shorten_it(self):
        self.runner._max_run_seconds = MINUTE
        self.assertIsNone(self._wait(MINUTE, lambda *_: None))
        self.assertEqual(self.clock.now - 1000.0, IDLE_LIMIT)


class DefaultsTests(unittest.TestCase):
    def test_the_cap_defaults_to_a_whole_night(self):
        runner = PlanningSessionRunner(
            session_manager=MagicMock(),
            defaults=StreamingSessionDefaults(),
        )
        self.assertEqual(runner._max_run_seconds, 8 * HOUR)


class WaitingForOperatorTests(unittest.TestCase):
    def test_a_pending_ask(self):
        session = SimpleNamespace(pending_control_request_tool=lambda: 'Bash')
        self.assertTrue(_is_waiting_for_operator(session))

    def test_nothing_pending(self):
        session = SimpleNamespace(pending_control_request_tool=lambda: '')
        self.assertFalse(_is_waiting_for_operator(session))

    def test_a_session_with_no_such_probe(self):
        self.assertFalse(_is_waiting_for_operator(SimpleNamespace()))

    def test_a_mock_never_looks_like_a_pending_ask(self):
        # A truthy MagicMock here would push the deadlines out forever.
        self.assertFalse(_is_waiting_for_operator(MagicMock()))


if __name__ == '__main__':
    unittest.main()
