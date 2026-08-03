"""The text helpers, pinned once — they used to be five forks with three contracts.

``normalized_text`` and friends were independently defined in ``agent_core_lib``,
``git_core_lib``, ``kato_core_lib``, ``provider_client_base`` and
``youtrack_core_lib``, and EIGHT test files re-tested them. The copies were not
equivalent, and every difference failed quietly — see the module docstring on
``utils_core_lib.text_utils``. ``ContractDivergenceTests`` at the bottom pins
the specific differences, so re-narrowing any of them fails here.
"""

import unittest
from collections.abc import Mapping
from types import MappingProxyType, SimpleNamespace

from omegaconf import OmegaConf

from utils_core_lib.utils_core_lib.text_utils import (
    alphanumeric_lower_text,
    condensed_lower_text,
    condensed_text,
    dict_from_mapping,
    list_from_mapping,
    normalized_lower_text,
    normalized_text,
    text_from_attr,
    text_from_mapping,
)


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

# ---------------------------------------------------------------------------
# The contract differences between the old forks. Each of these FAILS against
# at least one copy that existed before consolidation.
# ---------------------------------------------------------------------------


class ContractDivergenceTests(unittest.TestCase):
    def test_dict_from_mapping_accepts_a_non_dict_mapping(self) -> None:
        # One fork gated the CONTAINER on ``isinstance(mapping, dict)``, the
        # other on ``Mapping``. This is the only case where they disagree, and
        # it is latent: no caller passes a non-dict Mapping today (they all
        # walk parsed-JSON payloads). Pinned so the wider gate isn't narrowed
        # back on the assumption that it was never doing anything.
        proxy = MappingProxyType({'outer': {'inner': 1}})
        self.assertIsInstance(proxy, Mapping)
        self.assertNotIsInstance(proxy, dict)
        self.assertEqual(dict_from_mapping(proxy, 'outer'), {'inner': 1})

    def test_list_from_mapping_accepts_a_non_dict_mapping(self) -> None:
        proxy = MappingProxyType({'items': [1, 2]})
        self.assertEqual(list_from_mapping(proxy, 'items'), [1, 2])

    def test_container_helpers_do_not_unwrap_an_omegaconf_node(self) -> None:
        # Worth stating explicitly because it is easy to assume otherwise: the
        # VALUE gate is ``dict``/``list``, and a nested omegaconf node is
        # neither, so these return empty for a live config under BOTH the old
        # gates. They are for parsed-JSON payloads. Config reads use
        # ``text_from_mapping`` (below), which does handle DictConfig.
        cfg = OmegaConf.create({'outer': {'inner': 1}, 'items': [1, 2]})
        self.assertIsInstance(cfg, Mapping)
        self.assertEqual(dict_from_mapping(cfg, 'outer'), {})
        self.assertEqual(list_from_mapping(cfg, 'items'), [])

    def test_text_from_mapping_accepts_a_non_dict_mapping(self) -> None:
        cfg = OmegaConf.create({'name': ' kept '})
        self.assertEqual(text_from_mapping(cfg, 'name'), 'kept')

    def test_text_from_mapping_is_duck_typed_not_mapping_gated(self) -> None:
        # Three forks required a real Mapping, which rejected the
        # ``SimpleNamespace(get=...)`` stand-ins tests use. Duck-typing is a
        # strict superset: identical for dict and for omegaconf configs.
        stand_in = SimpleNamespace(get=lambda key, default='': {'k': ' v '}.get(key, default))
        self.assertEqual(text_from_mapping(stand_in, 'k'), 'v')

    def test_text_from_mapping_rejects_an_object_without_a_callable_get(self) -> None:
        self.assertEqual(text_from_mapping(SimpleNamespace(get='not callable'), 'k', 'fb'), 'fb')
        self.assertEqual(text_from_mapping(object(), 'k', 'fb'), 'fb')

    def test_text_from_attr_returns_empty_for_a_present_but_falsy_attribute(self) -> None:
        # The git_core_lib fork had ``or default`` here, so a present-but-empty
        # attribute fell back to the default. The default covers "the attribute
        # isn't there", not "the attribute is empty".
        self.assertEqual(text_from_attr(SimpleNamespace(name=''), 'name', 'fallback'), '')
        self.assertEqual(text_from_attr(SimpleNamespace(name=None), 'name', 'fallback'), '')

    def test_text_from_attr_uses_the_default_only_when_absent(self) -> None:
        self.assertEqual(text_from_attr(SimpleNamespace(), 'missing', ' fallback '), 'fallback')

    def test_text_from_attr_takes_the_attribute_name_by_keyword(self) -> None:
        # The git fork named this parameter ``key``; any keyword call would
        # have raised TypeError against one of the two copies.
        self.assertEqual(text_from_attr(SimpleNamespace(a=' x '), attribute='a'), 'x')


if __name__ == '__main__':
    unittest.main()
