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
    resolved_model_of_event,
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


class ResolvedModelTests(unittest.TestCase):
    """The window must be sized from the RESOLVED id, never the alias.

    Regression: sizing off the configured ``opus`` gave a 200k window for a
    session actually running a 1M-context model, so a healthy conversation
    rendered "0% left" in red and told the operator to compact a session with
    three quarters of its window free.
    """

    def test_alias_carries_no_window_information(self) -> None:
        # An alias must NOT be treated as the standard window — it could
        # resolve either way, and guessing is what produced the false alarm.
        self.assertEqual(resolved_model_of_event({'message': {'model': 'opus'}}), 'opus')
        self.assertEqual(context_window_tokens('claude-opus-5[1m]'), 1_000_000)
        self.assertEqual(context_window_tokens('claude-opus-5'), 200_000)

    def test_assistant_turn_reports_the_resolved_id(self) -> None:
        self.assertEqual(
            resolved_model_of_event(
                {'type': 'assistant', 'message': {'model': 'claude-opus-5[1m]'}},
            ),
            'claude-opus-5[1m]',
        )

    def test_top_level_model_is_also_read(self) -> None:
        self.assertEqual(
            resolved_model_of_event({'type': 'system', 'model': 'claude-haiku-4-5'}),
            'claude-haiku-4-5',
        )

    def test_events_without_a_model_yield_empty(self) -> None:
        for event in ({'type': 'result'}, {'message': {}}, {'message': 'x'},
                      {}, None, 'nope'):
            with self.subTest(repr(event)[:24]):
                self.assertEqual(resolved_model_of_event(event), '')

    def test_a_long_context_session_is_not_reported_as_full(self) -> None:
        # The exact shape of the bug: 250k used, 1M window → 75% left, not 0%.
        limit = context_window_tokens('claude-opus-5[1m]')
        used = 250_000
        self.assertEqual(round((limit - used) / limit * 100), 75)


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
