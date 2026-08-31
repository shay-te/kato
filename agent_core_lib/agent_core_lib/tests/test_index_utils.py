"""Tests for the shared session-index primitives.

These four steps were forked across the transports and had already drifted:
a 200-character preview came back ellipsised from one and cut bare from the
other, and ``max_results=-1`` meant "unbounded" in one and "drop the last row"
in the other. The divergences are pinned here so one behaviour is now the
behaviour.
"""

from __future__ import annotations

import unittest

from agent_core_lib.agent_core_lib.session.index_utils import (
    PREVIEW_LENGTH,
    cap_results,
    clip_preview,
    matches_query,
    parse_jsonl_dict_line,
    text_from_content,
)


class ParseJsonlDictLineTests(unittest.TestCase):
    def test_it_parses_a_dict_line(self) -> None:
        self.assertEqual(parse_jsonl_dict_line('{"a": 1}\n'), {'a': 1})

    def test_a_blank_line_is_none(self) -> None:
        self.assertIsNone(parse_jsonl_dict_line('   \n'))

    def test_a_truncated_line_is_none_not_a_raise(self) -> None:
        # A store being written while it is read routinely ends mid-line.
        self.assertIsNone(parse_jsonl_dict_line('{"type": "respo'))

    def test_a_non_dict_payload_is_none(self) -> None:
        self.assertIsNone(parse_jsonl_dict_line('[1, 2]'))
        self.assertIsNone(parse_jsonl_dict_line('"just a string"'))

    def test_none_input_is_none(self) -> None:
        self.assertIsNone(parse_jsonl_dict_line(None))


class TextFromContentTests(unittest.TestCase):
    def test_a_plain_string_is_returned(self) -> None:
        self.assertEqual(text_from_content('hello'), 'hello')

    def test_text_blocks_are_joined(self) -> None:
        self.assertEqual(
            text_from_content([{'text': 'a'}, {'text': 'b'}]), 'a b',
        )

    def test_bare_strings_in_a_list_are_read(self) -> None:
        self.assertEqual(text_from_content(['a', 'b']), 'a b')

    def test_types_restricts_which_parts_are_read(self) -> None:
        content = [
            {'type': 'input_text', 'text': 'wanted'},
            {'type': 'thinking', 'text': 'skipped'},
        ]
        self.assertEqual(
            text_from_content(content, types=('input_text',)), 'wanted',
        )

    def test_without_types_every_texted_part_is_read(self) -> None:
        content = [{'type': 'whatever', 'text': 'a'}, {'text': 'b'}]
        self.assertEqual(text_from_content(content), 'a b')

    def test_the_separator_is_configurable(self) -> None:
        self.assertEqual(
            text_from_content([{'text': 'a'}, {'text': 'b'}], separator='\n'),
            'a\nb',
        )

    def test_junk_is_ignored_not_raised(self) -> None:
        self.assertEqual(text_from_content([None, 42, {'no': 'text'}]), '')
        self.assertEqual(text_from_content(None), '')


class ClipPreviewTests(unittest.TestCase):
    def test_whitespace_is_collapsed_to_one_line(self) -> None:
        self.assertEqual(clip_preview('a  b\n\nc'), 'a b c')

    def test_a_short_text_is_untouched(self) -> None:
        self.assertEqual(clip_preview('short'), 'short')

    def test_a_long_text_is_clipped_with_an_ellipsis(self) -> None:
        # THE DIVERGENCE: one copy cut bare mid-word, so a truncated row read
        # as a message that simply ended there.
        clipped = clip_preview('x' * 400)
        self.assertEqual(len(clipped), PREVIEW_LENGTH)
        self.assertTrue(clipped.endswith('…'))

    def test_it_never_exceeds_the_limit(self) -> None:
        # The ellipsis replaces a character rather than being appended past it.
        self.assertLessEqual(len(clip_preview('y' * 400, 10)), 10)

    def test_a_zero_length_does_not_raise(self) -> None:
        self.assertEqual(clip_preview('abc', 0), '…')

    def test_none_is_empty(self) -> None:
        self.assertEqual(clip_preview(None), '')


class MatchesQueryTests(unittest.TestCase):
    def test_an_empty_needle_matches_everything(self) -> None:
        self.assertTrue(matches_query('', 'anything'))
        self.assertTrue(matches_query('   ', 'anything'))

    def test_it_ignores_case(self) -> None:
        self.assertTrue(matches_query('AUTH', 'fix the auth bug'))
        self.assertTrue(matches_query('auth', 'fix the AUTH bug'))

    def test_any_field_can_match(self) -> None:
        self.assertTrue(matches_query('beta', '/work/alpha', 'beta message'))

    def test_a_miss_is_false(self) -> None:
        self.assertFalse(matches_query('nope', '/work/alpha', 'a message'))

    def test_a_match_cannot_straddle_two_fields(self) -> None:
        # Joining fields bare would let the tail of one plus the head of the
        # next produce a spurious hit.
        self.assertFalse(matches_query('alphabeta', 'alpha', 'beta'))

    def test_none_fields_are_tolerated(self) -> None:
        self.assertTrue(matches_query('a', None, 'a'))


class CapResultsTests(unittest.TestCase):
    def test_it_caps(self) -> None:
        self.assertEqual(cap_results([1, 2, 3], 2), [1, 2])

    def test_a_negative_cap_means_unbounded(self) -> None:
        # THE DIVERGENCE: ``rows[:-1]`` silently means "drop the last row".
        self.assertEqual(cap_results([1, 2, 3], -1), [1, 2, 3])

    def test_zero_returns_nothing(self) -> None:
        self.assertEqual(cap_results([1, 2, 3], 0), [])

    def test_a_cap_beyond_the_end_returns_everything(self) -> None:
        self.assertEqual(cap_results([1, 2], 99), [1, 2])


if __name__ == '__main__':
    unittest.main()
