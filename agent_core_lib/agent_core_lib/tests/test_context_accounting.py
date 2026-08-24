"""Shared context accounting: guarded token sums and the disproved-window rule.

Both of these feed an operator-facing reading ("how full is this chat?"), and
a wrong number is worse than no number: it either pushes someone into
compacting a session with plenty of room, or lets them hit the wall mid-task.
So the tests are mostly about junk input producing 0, not a guess.
"""

from __future__ import annotations

import unittest

from agent_core_lib.agent_core_lib.helpers.context_accounting import (
    sum_usage_tokens,
    widen_window_to_observed,
)

KEYS = ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')


class SumUsageTokensTests(unittest.TestCase):
    def test_sums_only_the_requested_keys(self) -> None:
        usage = {
            'input_tokens': 10,
            'cache_read_input_tokens': 90,
            'cache_creation_input_tokens': 5,
            'output_tokens': 1000,          # what the turn produced, not its cost
        }

        self.assertEqual(sum_usage_tokens(usage, KEYS), 105)

    def test_missing_and_null_keys_contribute_nothing(self) -> None:
        self.assertEqual(sum_usage_tokens({'input_tokens': None}, KEYS), 0)
        self.assertEqual(sum_usage_tokens({}, KEYS), 0)

    def test_a_bool_is_skipped_not_counted_as_one(self) -> None:
        # ``True + 10`` is 11 in Python — the silent corruption this guards.
        usage = {'input_tokens': True, 'cache_read_input_tokens': 10}

        self.assertEqual(sum_usage_tokens(usage, KEYS), 10)

    def test_floats_are_truncated_to_int(self) -> None:
        self.assertEqual(sum_usage_tokens({'input_tokens': 10.9}, KEYS), 10)

    def test_a_negative_total_reports_zero(self) -> None:
        self.assertEqual(sum_usage_tokens({'input_tokens': -50}, KEYS), 0)

    def test_a_non_mapping_payload_is_zero(self) -> None:
        for junk in (None, [], 'usage', 42):
            self.assertEqual(sum_usage_tokens(junk, KEYS), 0)

    def test_string_numbers_are_not_coerced(self) -> None:
        # A CLI that starts sending strings should read as unknown, not as a
        # number this module invented by parsing.
        self.assertEqual(sum_usage_tokens({'input_tokens': '900'}, KEYS), 0)


class WidenWindowToObservedTests(unittest.TestCase):
    def test_normal_usage_leaves_the_limit_alone(self) -> None:
        self.assertEqual(
            widen_window_to_observed(200_000, 90_000, ceiling=1_000_000), 200_000,
        )

    def test_usage_above_the_limit_widens_to_the_ceiling(self) -> None:
        self.assertEqual(
            widen_window_to_observed(200_000, 400_000, ceiling=1_000_000), 1_000_000,
        )

    def test_usage_above_even_the_ceiling_reports_the_usage(self) -> None:
        self.assertEqual(
            widen_window_to_observed(200_000, 1_500_000, ceiling=1_000_000), 1_500_000,
        )

    def test_an_unknown_window_stays_unknown(self) -> None:
        # 0 means "render unknown". Inventing a window from usage would put a
        # confident percentage on a model nobody has sized.
        self.assertEqual(widen_window_to_observed(0, 90_000, ceiling=1_000_000), 0)

    def test_usage_exactly_at_the_limit_is_not_widened(self) -> None:
        self.assertEqual(
            widen_window_to_observed(200_000, 200_000, ceiling=1_000_000), 200_000,
        )

    def test_junk_inputs_degrade_to_zero_rather_than_guessing(self) -> None:
        self.assertEqual(widen_window_to_observed(None, None, ceiling=1_000_000), 0)
        self.assertEqual(widen_window_to_observed('200k', 10, ceiling=1_000_000), 0)
        self.assertEqual(widen_window_to_observed(True, 10, ceiling=1_000_000), 0)

    def test_negative_usage_cannot_shrink_the_limit(self) -> None:
        self.assertEqual(
            widen_window_to_observed(200_000, -5, ceiling=1_000_000), 200_000,
        )


if __name__ == '__main__':
    unittest.main()
