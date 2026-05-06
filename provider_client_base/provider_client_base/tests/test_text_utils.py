import logging
import unittest

from provider_client_base.provider_client_base.helpers.logging_utils import configure_logger
from provider_client_base.provider_client_base.helpers.text_utils import (
    condensed_text,
    dict_from_mapping,
    list_from_mapping,
    normalized_text,
    text_from_attr,
    text_from_mapping,
)


# ---------------------------------------------------------------------------
# normalized_text
# ---------------------------------------------------------------------------


class NormalizedTextTests(unittest.TestCase):
    def test_plain_string_returned(self):
        self.assertEqual(normalized_text('hello'), 'hello')

    def test_strips_leading_whitespace(self):
        self.assertEqual(normalized_text('  hi'), 'hi')

    def test_strips_trailing_whitespace(self):
        self.assertEqual(normalized_text('hi  '), 'hi')

    def test_strips_both_sides(self):
        self.assertEqual(normalized_text('  hi  '), 'hi')

    def test_none_returns_empty(self):
        self.assertEqual(normalized_text(None), '')

    def test_empty_string_returns_empty(self):
        self.assertEqual(normalized_text(''), '')

    def test_whitespace_only_returns_empty(self):
        self.assertEqual(normalized_text('   '), '')

    def test_integer_coerced_to_string(self):
        self.assertEqual(normalized_text(42), '42')

    def test_float_coerced_to_string(self):
        self.assertEqual(normalized_text(3.14), '3.14')

    def test_false_returns_empty(self):
        self.assertEqual(normalized_text(False), '')

    def test_zero_returns_empty(self):
        self.assertEqual(normalized_text(0), '')

    def test_list_coerced_to_string(self):
        result = normalized_text(['a', 'b'])
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)


# ---------------------------------------------------------------------------
# condensed_text
# ---------------------------------------------------------------------------


class CondensedTextTests(unittest.TestCase):
    def test_collapses_internal_spaces(self):
        self.assertEqual(condensed_text('hello   world'), 'hello world')

    def test_collapses_tabs_and_newlines(self):
        self.assertEqual(condensed_text('hello\t\nworld'), 'hello world')

    def test_strips_outer_whitespace(self):
        self.assertEqual(condensed_text('  hello  '), 'hello')

    def test_none_returns_empty(self):
        self.assertEqual(condensed_text(None), '')

    def test_empty_string_returns_empty(self):
        self.assertEqual(condensed_text(''), '')

    def test_whitespace_only_returns_empty(self):
        self.assertEqual(condensed_text('   \t\n  '), '')

    def test_single_word_unchanged(self):
        self.assertEqual(condensed_text('hello'), 'hello')

    def test_integer_coerced_and_returned(self):
        self.assertEqual(condensed_text(99), '99')

    def test_multiline_string_condensed(self):
        self.assertEqual(condensed_text('line one\nline two\nline three'), 'line one line two line three')


# ---------------------------------------------------------------------------
# text_from_attr
# ---------------------------------------------------------------------------


class TextFromAttrTests(unittest.TestCase):
    def test_returns_attribute_value(self):
        class Obj:
            name = 'Alice'
        self.assertEqual(text_from_attr(Obj(), 'name'), 'Alice')

    def test_missing_attribute_returns_default_empty(self):
        self.assertEqual(text_from_attr(object(), 'missing'), '')

    def test_missing_attribute_with_custom_default(self):
        self.assertEqual(text_from_attr(object(), 'missing', 'fallback'), 'fallback')

    def test_none_attribute_value_returns_empty(self):
        class Obj:
            name = None
        self.assertEqual(text_from_attr(Obj(), 'name'), '')

    def test_strips_whitespace_from_value(self):
        class Obj:
            name = '  Bob  '
        self.assertEqual(text_from_attr(Obj(), 'name'), 'Bob')

    def test_integer_attribute_coerced_to_string(self):
        class Obj:
            count = 7
        self.assertEqual(text_from_attr(Obj(), 'count'), '7')

    def test_none_object_missing_attr_returns_default(self):
        self.assertEqual(text_from_attr(None, 'anything'), '')


# ---------------------------------------------------------------------------
# text_from_mapping
# ---------------------------------------------------------------------------


class TextFromMappingTests(unittest.TestCase):
    def test_returns_value_for_key(self):
        self.assertEqual(text_from_mapping({'key': 'value'}, 'key'), 'value')

    def test_missing_key_returns_empty_default(self):
        self.assertEqual(text_from_mapping({'key': 'value'}, 'other'), '')

    def test_missing_key_with_custom_default(self):
        self.assertEqual(text_from_mapping({}, 'key', 'fallback'), 'fallback')

    def test_none_value_returns_empty(self):
        self.assertEqual(text_from_mapping({'key': None}, 'key'), '')

    def test_strips_whitespace(self):
        self.assertEqual(text_from_mapping({'key': '  hello  '}, 'key'), 'hello')

    def test_integer_value_coerced_to_string(self):
        self.assertEqual(text_from_mapping({'key': 42}, 'key'), '42')

    def test_none_mapping_returns_empty(self):
        self.assertEqual(text_from_mapping(None, 'key'), '')

    def test_non_mapping_type_returns_empty(self):
        self.assertEqual(text_from_mapping('not-a-dict', 'key'), '')

    def test_non_mapping_type_returns_default(self):
        self.assertEqual(text_from_mapping([], 'key', 'fallback'), 'fallback')

    def test_nested_dict_key_works(self):
        data = {'outer': {'inner': 'value'}}
        result = text_from_mapping(data, 'outer')
        self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# dict_from_mapping
# ---------------------------------------------------------------------------


class DictFromMappingTests(unittest.TestCase):
    def test_returns_dict_value(self):
        inner = {'a': 1}
        self.assertEqual(dict_from_mapping({'key': inner}, 'key'), inner)

    def test_missing_key_returns_empty_dict(self):
        self.assertEqual(dict_from_mapping({'key': 'val'}, 'other'), {})

    def test_non_dict_value_returns_empty_dict(self):
        self.assertEqual(dict_from_mapping({'key': 'string'}, 'key'), {})

    def test_list_value_returns_empty_dict(self):
        self.assertEqual(dict_from_mapping({'key': [1, 2]}, 'key'), {})

    def test_none_value_returns_empty_dict(self):
        self.assertEqual(dict_from_mapping({'key': None}, 'key'), {})

    def test_none_mapping_returns_empty_dict(self):
        self.assertEqual(dict_from_mapping(None, 'key'), {})

    def test_non_mapping_returns_empty_dict(self):
        self.assertEqual(dict_from_mapping('not-a-dict', 'key'), {})

    def test_empty_dict_value_returned(self):
        self.assertEqual(dict_from_mapping({'key': {}}, 'key'), {})

    def test_list_mapping_returns_empty_dict(self):
        self.assertEqual(dict_from_mapping([1, 2, 3], 0), {})


# ---------------------------------------------------------------------------
# list_from_mapping
# ---------------------------------------------------------------------------


class ListFromMappingTests(unittest.TestCase):
    def test_returns_list_value(self):
        items = [1, 2, 3]
        self.assertEqual(list_from_mapping({'key': items}, 'key'), items)

    def test_missing_key_returns_empty_list(self):
        self.assertEqual(list_from_mapping({'key': 'val'}, 'other'), [])

    def test_non_list_value_returns_empty_list(self):
        self.assertEqual(list_from_mapping({'key': 'string'}, 'key'), [])

    def test_dict_value_returns_empty_list(self):
        self.assertEqual(list_from_mapping({'key': {'a': 1}}, 'key'), [])

    def test_none_value_returns_empty_list(self):
        self.assertEqual(list_from_mapping({'key': None}, 'key'), [])

    def test_none_mapping_returns_empty_list(self):
        self.assertEqual(list_from_mapping(None, 'key'), [])

    def test_non_mapping_returns_empty_list(self):
        self.assertEqual(list_from_mapping(42, 'key'), [])

    def test_empty_list_value_returned(self):
        self.assertEqual(list_from_mapping({'key': []}, 'key'), [])

    def test_tuple_mapping_returns_empty_list(self):
        self.assertEqual(list_from_mapping((1, 2), 0), [])


# ---------------------------------------------------------------------------
# configure_logger
# ---------------------------------------------------------------------------


class ConfigureLoggerTests(unittest.TestCase):
    def test_returns_logger(self):
        logger = configure_logger('test_logger')
        self.assertIsInstance(logger, logging.Logger)

    def test_logger_has_correct_name(self):
        logger = configure_logger('my_service')
        self.assertEqual(logger.name, 'my_service')

    def test_same_name_returns_same_instance(self):
        logger1 = configure_logger('shared')
        logger2 = configure_logger('shared')
        self.assertIs(logger1, logger2)

    def test_different_names_return_different_loggers(self):
        logger1 = configure_logger('service_a')
        logger2 = configure_logger('service_b')
        self.assertIsNot(logger1, logger2)
        self.assertNotEqual(logger1.name, logger2.name)
