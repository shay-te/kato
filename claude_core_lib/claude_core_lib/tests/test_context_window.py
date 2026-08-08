"""Context-window accounting behind the composer's usage indicator.

The indicator exists so the OPERATOR decides when to ``/compact`` — the host
never restarts a session on its own. That makes wrong numbers worse than no
numbers:
an inflated reading pushes someone into compacting a session with plenty of
room, a deflated one lets them hit the wall mid-task. Hence "unknown is 0, and
the UI must say unknown", never a guess.
"""
from __future__ import annotations

import unittest

from claude_core_lib.claude_core_lib.helpers.context_window import (
    context_window_tokens,
    prompt_tokens_from_usage,
    usage_of_event,
)


class ContextWindowTests(unittest.TestCase):
    def test_standard_models_report_the_standard_window(self) -> None:
        for model in ('opus', 'sonnet', 'haiku', 'claude-opus-5',
                      'claude-haiku-4-5-20251001'):
            with self.subTest(model):
                self.assertEqual(context_window_tokens(model), 200_000)

    def test_long_context_variants_are_recognised(self) -> None:
        for model in ('claude-opus-5[1m]', 'claude-sonnet-5[1M]',
                      'claude-fable-5[1m]'):
            with self.subTest(model):
                self.assertEqual(context_window_tokens(model), 1_000_000)

    def test_unknown_model_reports_zero_not_a_guess(self) -> None:
        # 0 is the UI's "unknown" signal. Returning the standard window here
        # would render a confident percentage for a window we never confirmed.
        for model in ('', None, '   '):
            with self.subTest(repr(model)):
                self.assertEqual(context_window_tokens(model), 0)


class PromptTokensTests(unittest.TestCase):
    def test_sums_the_prompt_side_only(self) -> None:
        # output_tokens belongs to the NEXT turn's prompt; counting it here
        # double-counts every turn.
        usage = {
            'input_tokens': 4957,
            'cache_creation_input_tokens': 3327,
            'cache_read_input_tokens': 15837,
            'output_tokens': 22,
        }
        self.assertEqual(prompt_tokens_from_usage(usage), 24_121)

    def test_missing_buckets_are_treated_as_zero(self) -> None:
        self.assertEqual(prompt_tokens_from_usage({'input_tokens': 10}), 10)

    def test_junk_payloads_are_zero_not_an_exception(self) -> None:
        for usage in (None, 'nope', 42, [], {}):
            with self.subTest(repr(usage)):
                self.assertEqual(prompt_tokens_from_usage(usage), 0)

    def test_non_numeric_and_bool_values_are_ignored(self) -> None:
        # ``True`` is an int in Python; counting it as 1 token would be silly
        # but silent.
        self.assertEqual(
            prompt_tokens_from_usage(
                {'input_tokens': True, 'cache_read_input_tokens': 'x',
                 'cache_creation_input_tokens': 5},
            ),
            5,
        )

    def test_negative_totals_clamp_to_zero(self) -> None:
        self.assertEqual(prompt_tokens_from_usage({'input_tokens': -100}), 0)


class UsageExtractionTests(unittest.TestCase):
    def test_assistant_event_nests_usage_under_message(self) -> None:
        event = {'type': 'assistant',
                 'message': {'usage': {'input_tokens': 7}}}
        self.assertEqual(usage_of_event(event), {'input_tokens': 7})

    def test_result_event_carries_usage_at_the_top_level(self) -> None:
        event = {'type': 'result', 'usage': {'input_tokens': 9}}
        self.assertEqual(usage_of_event(event), {'input_tokens': 9})

    def test_events_without_usage_yield_an_empty_mapping(self) -> None:
        for event in ({'type': 'system'}, {'message': 'notadict'},
                      {'message': {}}, {}, None, 'nope'):
            with self.subTest(repr(event)[:30]):
                self.assertEqual(usage_of_event(event), {})


if __name__ == '__main__':
    unittest.main()
