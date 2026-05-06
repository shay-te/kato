"""Tests for youtrack_core_lib.helpers.text_utils."""
from __future__ import annotations

import unittest

from youtrack_core_lib.youtrack_core_lib.helpers.text_utils import (
    alphanumeric_lower_text,
    condensed_lower_text,
    normalized_lower_text,
    normalized_text,
    text_from_mapping,
)


class NormalizedTextTests(unittest.TestCase):
    def test_plain_string(self):
        self.assertEqual(normalized_text('hello'), 'hello')

    def test_strips_whitespace(self):
        self.assertEqual(normalized_text('  hello  '), 'hello')

    def test_none_returns_empty(self):
        self.assertEqual(normalized_text(None), '')

    def test_empty_string_returns_empty(self):
        self.assertEqual(normalized_text(''), '')

    def test_integer(self):
        self.assertEqual(normalized_text(42), '42')

    def test_zero_returns_empty(self):
        # 0 is falsy, so `str(0 or '')` → ''
        self.assertEqual(normalized_text(0), '')

    def test_false_returns_empty(self):
        self.assertEqual(normalized_text(False), '')

    def test_list_not_empty(self):
        self.assertEqual(normalized_text([1, 2]), '[1, 2]')

    def test_preserves_internal_spaces(self):
        self.assertEqual(normalized_text('  a  b  '), 'a  b')


class NormalizedLowerTextTests(unittest.TestCase):
    def test_lower_cased(self):
        self.assertEqual(normalized_lower_text('HELLO'), 'hello')

    def test_strips_and_lowercases(self):
        self.assertEqual(normalized_lower_text('  WORLD  '), 'world')

    def test_none_returns_empty(self):
        self.assertEqual(normalized_lower_text(None), '')

    def test_mixed_case(self):
        self.assertEqual(normalized_lower_text('CamelCase'), 'camelcase')


class CondensedLowerTextTests(unittest.TestCase):
    def test_collapses_spaces(self):
        self.assertEqual(condensed_lower_text('  hello   world  '), 'hello world')

    def test_lowercases(self):
        self.assertEqual(condensed_lower_text('Hello World'), 'hello world')

    def test_none_returns_empty(self):
        self.assertEqual(condensed_lower_text(None), '')

    def test_single_word(self):
        self.assertEqual(condensed_lower_text('  WORD  '), 'word')

    def test_tabs_and_newlines_collapsed(self):
        self.assertEqual(condensed_lower_text('a\t\nb'), 'a b')


class AlphanumericLowerTextTests(unittest.TestCase):
    def test_removes_punctuation(self):
        self.assertEqual(alphanumeric_lower_text('hello-world!'), 'helloworld')

    def test_lowercases(self):
        self.assertEqual(alphanumeric_lower_text('ABC'), 'abc')

    def test_none_returns_empty(self):
        self.assertEqual(alphanumeric_lower_text(None), '')

    def test_strips_spaces(self):
        self.assertEqual(alphanumeric_lower_text('In Review'), 'inreview')

    def test_digits_kept(self):
        self.assertEqual(alphanumeric_lower_text('abc123'), 'abc123')

    def test_only_special_chars(self):
        self.assertEqual(alphanumeric_lower_text('!@#$%'), '')

    def test_unicode_letters_kept(self):
        result = alphanumeric_lower_text('Héllo')
        self.assertIn('llo', result)


class TextFromMappingTests(unittest.TestCase):
    def test_returns_value_for_key(self):
        self.assertEqual(text_from_mapping({'a': 'b'}, 'a'), 'b')

    def test_returns_default_for_missing_key(self):
        self.assertEqual(text_from_mapping({'a': 'b'}, 'x'), '')

    def test_explicit_default(self):
        self.assertEqual(text_from_mapping({'a': 'b'}, 'x', 'fallback'), 'fallback')

    def test_none_mapping_returns_empty(self):
        self.assertEqual(text_from_mapping(None, 'key'), '')

    def test_none_mapping_returns_normalized_default(self):
        self.assertEqual(text_from_mapping(None, 'key', '  hi  '), 'hi')

    def test_non_mapping_returns_empty(self):
        self.assertEqual(text_from_mapping('not a dict', 'key'), '')

    def test_strips_value(self):
        self.assertEqual(text_from_mapping({'k': '  v  '}, 'k'), 'v')

    def test_none_value_returns_empty(self):
        self.assertEqual(text_from_mapping({'k': None}, 'k'), '')

    def test_integer_value_stringified(self):
        self.assertEqual(text_from_mapping({'k': 99}, 'k'), '99')


if __name__ == '__main__':
    unittest.main()
