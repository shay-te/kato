"""What a call costs at the published per-token rates.

Every expected figure is worked out by hand from the rate card (dollars per
million tokens), so a wrong rate, a wrong multiplier or a wrong tier boundary
fails here instead of reaching an operator as a believable-looking dollar sign.
"""

from __future__ import annotations

import unittest

from claude_core_lib.claude_core_lib.helpers.model_pricing import (
    COST_PARTS,
    PRICES_AS_OF,
    add_cost,
    empty_cost,
    is_priced,
    message_cost,
    rounded_cost,
)

MILLION = 1_000_000


def _counts(inp=0, out=0, read=0, write=0) -> dict:
    return {'input': inp, 'output': out, 'cache_read': read, 'cache_write': write}


class MessageCostTests(unittest.TestCase):
    def test_a_million_tokens_of_each_kind_is_priced_at_its_own_rate(self) -> None:
        # Opus 5.5: $4 in, $20 out, $0.20 cache read.
        self.assertAlmostEqual(
            message_cost('claude-opus-5-5', _counts(inp=MILLION))['fresh'], 4.0,
        )
        self.assertAlmostEqual(
            message_cost('claude-opus-5-5', _counts(out=MILLION))['output'], 20.0,
        )
        self.assertAlmostEqual(
            message_cost('claude-opus-5-5', _counts(read=MILLION))['cache_read'], 0.20,
        )

    def test_a_cache_read_is_not_a_tenth_of_input_everywhere(self) -> None:
        reads = {
            'claude-fable-5-1': 0.25,     # 0.025x of $10
            'claude-fable-5': 1.00,       # 0.1x of $10
            'claude-opus-5-5': 0.20,      # 0.05x of $4
            'claude-opus-5': 0.50,        # 0.1x of $5
            'claude-opus-4-8': 0.50,
            'claude-sonnet-5-5': 0.20,    # 0.1x of $2
            'claude-sonnet-4-6': 0.30,    # 0.1x of $3
            'claude-haiku-4-5': 0.10,     # 0.1x of $1
        }
        for model, expected in reads.items():
            with self.subTest(model=model):
                cost = message_cost(model, _counts(read=MILLION))
                self.assertAlmostEqual(cost['cache_read'], expected)

    def test_output_and_input_rates_per_model(self) -> None:
        rates = {
            'claude-fable-5-1': (10.0, 50.0),
            'claude-opus-5': (5.0, 25.0),
            'claude-opus-4-6': (5.0, 25.0),
            'claude-sonnet-5': (2.0, 10.0),
            'claude-sonnet-4-6': (3.0, 15.0),
            'claude-haiku-4-5': (1.0, 5.0),
        }
        for model, (rate_in, rate_out) in rates.items():
            with self.subTest(model=model):
                cost = message_cost(model, _counts(inp=MILLION, out=MILLION))
                self.assertAlmostEqual(cost['fresh'], rate_in)
                self.assertAlmostEqual(cost['output'], rate_out)

    def test_cache_writes_cost_more_than_input_and_a_1h_entry_costs_most(self) -> None:
        # 5-minute entry: 1.25x the $4 input rate. 1-hour entry: 2x.
        five = message_cost('claude-opus-5-5', _counts(write=MILLION))
        one_hour = message_cost('claude-opus-5-5', _counts(write=MILLION), MILLION)
        self.assertAlmostEqual(five['fresh'], 5.0)
        self.assertAlmostEqual(one_hour['fresh'], 8.0)

    def test_a_write_split_between_the_two_lifetimes_is_priced_per_part(self) -> None:
        # 600k x 1.25 x $4 + 400k x 2 x $4, per million.
        cost = message_cost('claude-opus-5-5', _counts(write=MILLION), 400_000)
        self.assertAlmostEqual(cost['fresh'], 3.0 + 3.2)

    def test_more_1h_tokens_than_were_written_are_clamped_not_trusted(self) -> None:
        clamped = message_cost('claude-opus-5-5', _counts(write=1000), 5000)
        all_1h = message_cost('claude-opus-5-5', _counts(write=1000), 1000)
        self.assertEqual(clamped, all_1h)
        negative = message_cost('claude-opus-5-5', _counts(write=1000), -50)
        self.assertEqual(negative, message_cost('claude-opus-5-5', _counts(write=1000), 0))

    def test_new_input_and_cache_writes_share_the_fresh_part(self) -> None:
        cost = message_cost('claude-opus-5-5', _counts(inp=MILLION, write=MILLION))
        self.assertAlmostEqual(cost['fresh'], 4.0 + 5.0)

    def test_missing_counters_are_zero(self) -> None:
        self.assertEqual(message_cost('claude-opus-5-5', {}), {
            'output': 0.0, 'fresh': 0.0, 'cache_read': 0.0,
        })

    # ----- Haiku 5.5: the rate card depends on the request's prompt length -----

    def test_haiku_5_5_uses_the_cheap_card_up_to_100k_prompt_tokens(self) -> None:
        # Prompt = input + cache read + cache write; exactly 100,000 is still cheap.
        cost = message_cost('claude-haiku-5-5', _counts(inp=100_000, out=MILLION))
        self.assertAlmostEqual(cost['fresh'], 0.01)
        self.assertAlmostEqual(cost['output'], 0.50)

    def test_haiku_5_5_switches_card_one_token_past_100k(self) -> None:
        cost = message_cost('claude-haiku-5-5', _counts(inp=100_001, out=MILLION))
        self.assertAlmostEqual(cost['fresh'], 100_001 * 0.50 / MILLION)
        self.assertAlmostEqual(cost['output'], 2.50)

    def test_haiku_5_5_prompt_length_counts_cached_tokens_too(self) -> None:
        # 60k new + 50k cache read = 110k prompt -> the dearer card for BOTH.
        cost = message_cost('claude-haiku-5-5', _counts(inp=60_000, read=50_000))
        self.assertAlmostEqual(cost['fresh'], 60_000 * 0.50 / MILLION)
        self.assertAlmostEqual(cost['cache_read'], 50_000 * 0.05 / MILLION)

    def test_other_models_have_no_prompt_length_tier(self) -> None:
        small = message_cost('claude-opus-5-5', _counts(inp=1000))['fresh'] / 1000
        large = message_cost('claude-opus-5-5', _counts(inp=900_000))['fresh'] / 900_000
        self.assertAlmostEqual(small, large)

    # ----- which models are priced -----

    def test_a_model_with_no_price_on_file_is_none_never_free(self) -> None:
        self.assertIsNone(message_cost('claude-made-up-9', _counts(out=MILLION)))
        self.assertIsNone(message_cost('', _counts(out=MILLION)))
        self.assertIsNone(message_cost(None, _counts(out=MILLION)))
        self.assertFalse(is_priced('claude-made-up-9'))
        self.assertFalse(is_priced(''))

    def test_the_model_id_is_read_as_the_api_wrote_it(self) -> None:
        for model in ('claude-opus-5-5[1m]', ' Claude-Opus-5-5 ', 'claude-haiku-4-5-20251001'):
            with self.subTest(model=model):
                self.assertTrue(is_priced(model))
                self.assertIsNotNone(message_cost(model, _counts(out=1)))
        # The snapshot date is stripped, so the price is the base model's.
        self.assertEqual(
            message_cost('claude-haiku-4-5-20251001', _counts(out=MILLION)),
            message_cost('claude-haiku-4-5', _counts(out=MILLION)),
        )

    def test_a_partial_date_is_not_mistaken_for_a_snapshot(self) -> None:
        self.assertFalse(is_priced('claude-opus-5-2026'))

    def test_the_price_date_is_published(self) -> None:
        self.assertRegex(PRICES_AS_OF, r'^\d{4}-\d{2}-\d{2}$')


class CostRecordTests(unittest.TestCase):
    def test_an_empty_record_has_every_part_and_a_total(self) -> None:
        cost = empty_cost()
        self.assertEqual(sorted(cost), sorted([*COST_PARTS, 'total']))
        self.assertTrue(all(value == 0.0 for value in cost.values()))

    def test_empty_records_are_independent(self) -> None:
        first, second = empty_cost(), empty_cost()
        first['output'] = 9.0
        self.assertEqual(second['output'], 0.0)

    def test_adding_sums_the_parts_and_recomputes_the_total(self) -> None:
        into = empty_cost()
        add_cost(into, {'output': 1.0, 'fresh': 2.0, 'cache_read': 3.0})
        add_cost(into, {'output': 0.5, 'fresh': 0.5, 'cache_read': 0.5})
        self.assertEqual(into, {'output': 1.5, 'fresh': 2.5, 'cache_read': 3.5, 'total': 7.5})

    def test_adding_nothing_changes_nothing(self) -> None:
        into = empty_cost()
        add_cost(into, None)
        add_cost(into, {})
        self.assertEqual(into, empty_cost())

    def test_the_total_given_by_the_other_record_is_ignored_in_favour_of_its_parts(self) -> None:
        into = add_cost(empty_cost(), {'output': 1.0, 'total': 999.0})
        self.assertEqual(into['total'], 1.0)

    def test_rounding_to_a_millionth_removes_float_noise(self) -> None:
        noisy = {'output': 0.1 + 0.2, 'fresh': 1 / 3, 'cache_read': 0.0000004, 'total': 0}
        rounded = rounded_cost(noisy)
        self.assertEqual(rounded['output'], 0.3)
        self.assertEqual(rounded['fresh'], 0.333333)
        self.assertEqual(rounded['cache_read'], 0.0)
        self.assertEqual(rounded['total'], round(0.3 + 0.333333, 6))


if __name__ == '__main__':
    unittest.main()
