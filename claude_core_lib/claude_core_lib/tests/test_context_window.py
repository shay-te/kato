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
    widen_window_to_observed,
)


class ContextWindowTests(unittest.TestCase):
    def test_current_generations_report_the_1m_window(self) -> None:
        # 1M is the STANDARD window for these families now — not an opt-in
        # variant. Sizing them at 200k is the bug this module exists to avoid.
        for model in ('claude-opus-5', 'claude-opus-4-8', 'claude-opus-4-6',
                      'claude-sonnet-5', 'claude-sonnet-4-6',
                      'claude-fable-5', 'claude-mythos-5'):
            with self.subTest(model):
                self.assertEqual(context_window_tokens(model), 1_000_000)

    def test_short_window_families_and_older_releases(self) -> None:
        for model in ('claude-haiku-4-5', 'claude-haiku-4-5-20251001',
                      'claude-opus-4-5', 'claude-opus-4-1',
                      'claude-sonnet-4-5', 'claude-sonnet-4-20250514'):
            with self.subTest(model):
                self.assertEqual(context_window_tokens(model), 200_000)

    def test_date_suffix_is_not_parsed_as_a_minor_version(self) -> None:
        # ``claude-sonnet-4-20250514`` is 4.0 with a date, not 4.20250514 —
        # reading it as a huge minor would clear the 4.6 gate and wrongly
        # report 1M for a 200k model.
        self.assertEqual(context_window_tokens('claude-sonnet-4-20250514'), 200_000)

    def test_explicit_long_context_marker_is_honoured(self) -> None:
        for model in ('claude-opus-5[1m]', 'claude-sonnet-5[1M]',
                      'claude-fable-5[1m]', 'claude-opus-4-5[1m]'):
            with self.subTest(model):
                self.assertEqual(context_window_tokens(model), 1_000_000)

    def test_bare_alias_uses_the_family_latest(self) -> None:
        # An alias always resolves to the LATEST of its family, so the
        # family's current window is a reading, not a guess.
        self.assertEqual(context_window_tokens('opus'), 1_000_000)
        self.assertEqual(context_window_tokens('sonnet'), 1_000_000)
        self.assertEqual(context_window_tokens('fable'), 1_000_000)
        self.assertEqual(context_window_tokens('haiku'), 200_000)

    def test_unknown_model_reports_zero_not_a_guess(self) -> None:
        # 0 is the UI's "unknown" signal. Returning a window here would render
        # a confident percentage for something we never identified.
        for model in ('', None, '   ', 'gpt-4o', 'claude-', 'llama-3'):
            with self.subTest(repr(model)):
                self.assertEqual(context_window_tokens(model), 0)


class ObservedFloorTests(unittest.TestCase):
    """Usage above the assumed window disproves the assumption."""

    def test_normal_case_leaves_the_limit_alone(self) -> None:
        self.assertEqual(widen_window_to_observed(1_000_000, 97_200), 1_000_000)
        self.assertEqual(widen_window_to_observed(200_000, 200_000), 200_000)

    def test_usage_beyond_the_limit_widens_it(self) -> None:
        # A model id we haven't learned, sized short: the session itself
        # proves the window is bigger than we assumed.
        self.assertEqual(widen_window_to_observed(200_000, 260_000), 1_000_000)

    def test_usage_beyond_every_known_window_reports_itself(self) -> None:
        self.assertEqual(widen_window_to_observed(200_000, 1_400_000), 1_400_000)

    def test_unknown_stays_unknown(self) -> None:
        # Never invent a window out of usage — 0 must survive so the meter
        # keeps rendering "unknown" rather than "100% full".
        self.assertEqual(widen_window_to_observed(0, 500_000), 0)

    def test_junk_inputs_are_zero_not_an_exception(self) -> None:
        for limit, used in ((None, 10), ('x', 10), (-5, 10), (True, 10)):
            with self.subTest(repr(limit)):
                self.assertEqual(widen_window_to_observed(limit, used), 0)


class ResolvedModelTests(unittest.TestCase):
    """The window is sized from the RESOLVED id — which drops ``[1m]``.

    Regression: the CLI accepts ``[1m]`` on ``--model`` but strips it from the
    id it reports back, so keying the window off that marker alone fell back
    to 200k for every session. A 97.2k conversation in a 1M window rendered
    "51% left" while the CLI's own ``/context`` reported 10% used.
    """

    def test_resolved_id_arrives_without_the_1m_marker(self) -> None:
        # Exactly what a real transcript carries for a 1M-window session.
        resolved = resolved_model_of_event(
            {'type': 'assistant', 'message': {'model': 'claude-opus-5'}},
        )
        self.assertEqual(resolved, 'claude-opus-5')
        self.assertEqual(context_window_tokens(resolved), 1_000_000)

    def test_the_reported_bug_no_longer_reproduces(self) -> None:
        # 97.2k used against the real 1M window is 90% left, not 51%.
        limit = context_window_tokens('claude-opus-5')
        used = 97_200
        self.assertEqual(round((limit - used) / limit * 100), 90)

    def test_alias_is_read_as_a_family(self) -> None:
        self.assertEqual(resolved_model_of_event({'message': {'model': 'opus'}}), 'opus')

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


class TrackedUsageSourceTests(unittest.TestCase):
    """Only ASSISTANT events may set the context figure.

    Regression: ``result`` usage is the turn's CUMULATIVE total across every
    API request in the agentic loop, and latest-wins let it overwrite the
    real figure at the end of every turn. A 122.6k conversation in a 1M
    window rendered "0% left" in red while ``/context`` reported 12% used.
    """

    @staticmethod
    def _session():
        from claude_core_lib.claude_core_lib.session.streaming import (
            StreamingClaudeSession,
        )
        return StreamingClaudeSession.__new__(StreamingClaudeSession)

    def _track(self, raws):
        import threading
        from types import SimpleNamespace
        session = self._session()
        session._context_usage_lock = threading.Lock()
        session._context_used_tokens = 0
        session._resolved_model = ''
        for raw in raws:
            session._track_context_usage(SimpleNamespace(raw=raw))
        return session.context_usage()

    def test_result_usage_never_sets_the_figure(self) -> None:
        assistant = {
            'type': 'assistant',
            'message': {
                'model': 'claude-opus-5',
                'usage': {'input_tokens': 2, 'cache_read_input_tokens': 122_600},
            },
        }
        # The same turn's result event, carrying the cumulative total.
        result = {'type': 'result', 'usage': {'cache_read_input_tokens': 990_000}}

        usage = self._track([assistant, result])

        self.assertEqual(usage['used_tokens'], 122_602)
        self.assertEqual(usage['limit_tokens'], 1_000_000)
        remaining = round(
            (usage['limit_tokens'] - usage['used_tokens'])
            / usage['limit_tokens'] * 100
        )
        self.assertEqual(remaining, 88)  # not 0

    def test_a_later_assistant_turn_still_updates(self) -> None:
        usage = self._track([
            {'type': 'assistant', 'message': {'model': 'claude-opus-5',
                                              'usage': {'input_tokens': 10_000}}},
            {'type': 'result', 'usage': {'input_tokens': 999_999}},
            {'type': 'assistant', 'message': {'model': 'claude-opus-5',
                                              'usage': {'input_tokens': 20_000}}},
        ])
        self.assertEqual(usage['used_tokens'], 20_000)

    def test_result_events_still_contribute_the_model_id(self) -> None:
        usage = self._track([{'type': 'result', 'model': 'claude-opus-5'}])
        self.assertEqual(usage['model'], 'claude-opus-5')
        self.assertEqual(usage['used_tokens'], 0)
