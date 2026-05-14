"""Regression: ``_parse_toggle_input`` covers ranges, lists, and edge cases.

Pin the picker's toggle-parser so future refactors don't accidentally
clip the last item of a range, drop duplicates, or break the "out of
range entries silently ignored" contract that lets a typo not nuke
the whole input.
"""

from __future__ import annotations

import unittest

from scripts.approve_repository import _parse_toggle_input


class ParseToggleInputTests(unittest.TestCase):

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(_parse_toggle_input('', 10), [])
        self.assertEqual(_parse_toggle_input('   ', 10), [])

    def test_single_index_returns_zero_based(self) -> None:
        # "1" is item 1 (1-indexed) → returns 0 (0-indexed).
        self.assertEqual(_parse_toggle_input('1', 10), [0])
        self.assertEqual(_parse_toggle_input('10', 10), [9])

    def test_comma_list_returns_each_zero_based(self) -> None:
        self.assertEqual(_parse_toggle_input('1,3,5', 10), [0, 2, 4])

    def test_space_list_returns_each_zero_based(self) -> None:
        self.assertEqual(_parse_toggle_input('1 3 5', 10), [0, 2, 4])

    def test_range_inclusive_of_endpoint(self) -> None:
        # The bug report claimed "1-10 on 10 items doesn't check item
        # 10" — this test pins the parser as inclusive of the
        # endpoint so the regression doesn't reappear.
        self.assertEqual(
            _parse_toggle_input('1-10', 10),
            [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        )

    def test_range_overflow_clamped_to_max(self) -> None:
        # "1-11" on a 10-item list silently drops 11 — typo
        # protection. The first 10 are still included.
        self.assertEqual(
            _parse_toggle_input('1-11', 10),
            [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        )

    def test_reversed_range_normalized_to_forward(self) -> None:
        # "5-1" is the same as "1-5".
        self.assertEqual(_parse_toggle_input('5-1', 10), [0, 1, 2, 3, 4])

    def test_single_element_range(self) -> None:
        self.assertEqual(_parse_toggle_input('3-3', 10), [2])

    def test_range_plus_singles_combined(self) -> None:
        # Picker's docs show this form: "1,3,5-7"
        self.assertEqual(
            _parse_toggle_input('1,3,5-7', 10),
            [0, 2, 4, 5, 6],
        )

    def test_out_of_range_single_index_dropped(self) -> None:
        # 99 doesn't exist → silently ignored.
        self.assertEqual(_parse_toggle_input('1,99,3', 10), [0, 2])

    def test_zero_and_negative_dropped(self) -> None:
        # Indices are 1-based — 0 and negatives must NOT map to
        # rows[-1] etc.
        self.assertEqual(_parse_toggle_input('0,1,-5', 10), [0])

    def test_non_numeric_tokens_dropped(self) -> None:
        # A typo like "abc" is skipped, the rest of the input still
        # works — operator doesn't have to re-type a long range.
        self.assertEqual(_parse_toggle_input('1,abc,3', 10), [0, 2])

    def test_repeated_index_returned_twice(self) -> None:
        # Documented behavior: duplicates are returned and the caller
        # toggles twice → back to original state. The doc string for
        # the picker tells operators not to type duplicates; we keep
        # the contract literal so the picker remains predictable.
        self.assertEqual(_parse_toggle_input('1,1', 10), [0, 0])

    def test_range_and_single_overlap_returned_in_input_order(self) -> None:
        # "1-3,2" → [0,1,2,1] — caller toggles 2 twice, leaving it
        # in the original state. Same documented behavior as above.
        self.assertEqual(_parse_toggle_input('1-3,2', 10), [0, 1, 2, 1])

    def test_max_index_one(self) -> None:
        # Single-item list — "1" works, "1-2" includes only 1.
        self.assertEqual(_parse_toggle_input('1', 1), [0])
        self.assertEqual(_parse_toggle_input('1-2', 1), [0])


if __name__ == '__main__':
    unittest.main()
