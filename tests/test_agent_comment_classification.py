"""Reading kato's own operational comments back off a ticket.

This logic used to live on ``TicketClientBase`` — a 462-line HTTP client class
with no subclasses, whose transport surface duplicated ``IssueClientBase``.
Only these classifiers had production callers, so they were extracted here and
the class deleted.

The retry-override guards below are the ones whose tests went with that class:
they are the reason kato does not treat its OWN "stopped working" comment as
an operator saying "go again", and they are guards, so nothing else fails when
they regress — the task simply stops being blocked when it should be, or stays
blocked when the operator has cleared it.
"""

from __future__ import annotations

import unittest

from kato_core_lib.data_layers.data.fields import TaskCommentFields
from kato_core_lib.helpers.agent_comment_classification import (
    AGENT_COMPLETION_COMMENT_PREFIX,
    active_execution_blocking_comment,
    is_agent_operational_comment,
    is_completion_comment,
    is_pre_start_blocking_comment,
    is_retry_override_comment,
)

BODY = TaskCommentFields.BODY


def _thread(*bodies: object) -> list[dict[str, object]]:
    return [{BODY: b} for b in bodies]


class RetryOverrideGuardTests(unittest.TestCase):
    """The two guards inside ``is_retry_override_comment``."""

    def test_katos_own_operational_comment_is_never_an_override(self) -> None:
        # Otherwise a blocking comment that happens to quote the phrase would
        # clear itself, and a permanently-failing task would loop forever.
        for own in (
            'Kato agent stopped working on this task: timeout',
            'Kato agent could not safely process this task: no repo',
            'Kato agent started working on this task',
            AGENT_COMPLETION_COMMENT_PREFIX + 'PROJ-1',
        ):
            self.assertFalse(is_retry_override_comment(own), own)

    def test_a_blocking_comment_quoting_the_phrase_cannot_clear_itself(self) -> None:
        blocking = 'Kato agent stopped working on this task: kato: retry approved'
        self.assertFalse(is_retry_override_comment(blocking))
        self.assertEqual(active_execution_blocking_comment(_thread(blocking)), blocking)

    def test_blank_and_whitespace_are_not_an_override(self) -> None:
        for blank in ('', '   ', '\t\n', None):
            self.assertFalse(is_retry_override_comment(blank), repr(blank))

    def test_the_operator_phrase_is_case_and_spacing_insensitive(self) -> None:
        for approved in ('kato: retry approved', 'KATO: RETRY APPROVED',
                         '  Kato   Retry   Approved  ', 'kato retry approved now'):
            self.assertTrue(is_retry_override_comment(approved), approved)

    def test_a_near_miss_is_not_an_override(self) -> None:
        for other in ('kato: retry denied', 'retry', 'please retry approved'):
            self.assertFalse(is_retry_override_comment(other), other)


class ThreadWalkDefensiveTests(unittest.TestCase):
    """Malformed entries must be skipped, not crash the pre-flight scan."""

    def test_non_dict_entries_are_skipped(self) -> None:
        blocking = 'Kato agent stopped working on this task: boom'
        self.assertEqual(
            active_execution_blocking_comment(['a string', None, 42, {BODY: blocking}]),
            blocking,
        )

    def test_entries_with_no_usable_body_are_skipped(self) -> None:
        blocking = 'Kato agent stopped working on this task: boom'
        self.assertEqual(
            active_execution_blocking_comment(
                _thread(None, '', '   ') + [{'other': 'x'}, {BODY: blocking}],
            ),
            blocking,
        )

    def test_no_comments_at_all_blocks_nothing(self) -> None:
        self.assertEqual(active_execution_blocking_comment(None), '')
        self.assertEqual(active_execution_blocking_comment([]), '')


class BlockClearAndRearmTests(unittest.TestCase):
    """The walk is oldest-to-newest and order-sensitive by design."""

    STOPPED = 'Kato agent stopped working on this task: boom'
    APPROVED = 'kato: retry approved'

    def test_an_override_after_a_block_clears_it(self) -> None:
        self.assertEqual(
            active_execution_blocking_comment(_thread(self.STOPPED, self.APPROVED)), '')

    def test_an_override_before_a_block_does_not_clear_it(self) -> None:
        self.assertEqual(
            active_execution_blocking_comment(_thread(self.APPROVED, self.STOPPED)),
            self.STOPPED)

    def test_a_later_block_re_arms_after_an_override(self) -> None:
        self.assertEqual(
            active_execution_blocking_comment(
                _thread(self.STOPPED, self.APPROVED, self.STOPPED)),
            self.STOPPED)

    def test_an_in_flight_run_blocks(self) -> None:
        started = 'Kato agent started working on this task'
        self.assertEqual(active_execution_blocking_comment(_thread(started)), started)

    def test_a_human_comment_neither_blocks_nor_clears(self) -> None:
        self.assertEqual(
            active_execution_blocking_comment(_thread(self.STOPPED, 'please fix it')),
            self.STOPPED)


class ClassifierTests(unittest.TestCase):
    def test_completion_comment(self) -> None:
        self.assertTrue(is_completion_comment(AGENT_COMPLETION_COMMENT_PREFIX + 'PROJ-1'))
        self.assertFalse(is_completion_comment('Kato agent started working on this task'))
        self.assertFalse(is_completion_comment(''))

    def test_pre_start_blocking_comment(self) -> None:
        self.assertTrue(is_pre_start_blocking_comment(
            'Kato agent could not safely process this task: nope'))
        # A comment posted AFTER work started is not a pre-start refusal.
        self.assertFalse(is_pre_start_blocking_comment(
            'Kato agent stopped working on this task: boom'))
        self.assertFalse(is_pre_start_blocking_comment('please fix it'))

    def test_agent_operational_comment_covers_every_prefix_kato_posts(self) -> None:
        for own in ('Kato agent started working on this task',
                    'Kato agent stopped working on this task: boom',
                    'Kato addressed review comment 42',
                    'Kato agent could not safely process this task: nope',
                    AGENT_COMPLETION_COMMENT_PREFIX + 'PROJ-1'):
            self.assertTrue(is_agent_operational_comment(own), own)
        self.assertFalse(is_agent_operational_comment('a human wrote this'))


if __name__ == '__main__':
    unittest.main()
