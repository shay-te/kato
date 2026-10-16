"""Token totals read from a Claude transcript.

Real files on disk, no mocks: the behaviours that matter here (a message
written as several lines, a file that grows between reads, a half-written
last line) are properties of the bytes, and a stub would only restate them.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from claude_core_lib.claude_core_lib.helpers.transcript_usage import (
    COUNT_NAMES,
    TranscriptUsageReader,
    add_usage,
    empty_usage,
)


def _usage(inp=0, out=0, read=0, write=0) -> dict:
    return {
        'input_tokens': inp,
        'output_tokens': out,
        'cache_read_input_tokens': read,
        'cache_creation_input_tokens': write,
    }


def _line(message_id, usage, *, model='claude-opus-5', at='2026-10-01T00:00:00Z',
          kind='assistant', **message_extra) -> str:
    message = {'id': message_id, 'model': model, 'usage': usage, **message_extra}
    return json.dumps({'type': kind, 'timestamp': at, 'message': message}) + '\n'


class TranscriptUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='transcript-usage-')
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / 'session.jsonl'
        self.reader = TranscriptUsageReader()

    def _write(self, *lines: str) -> None:
        self.path.write_text(''.join(lines), encoding='utf-8')

    def _append(self, *lines: str) -> None:
        with open(self.path, 'a', encoding='utf-8') as handle:
            handle.write(''.join(lines))

    # ----- counting -----

    def test_adds_up_the_four_counters_and_the_total(self) -> None:
        self._write(
            _line('m1', _usage(10, 20, 1000, 5)),
            _line('m2', _usage(1, 2, 300, 4)),
        )
        usage = self.reader.read(self.path)
        self.assertEqual(usage['input'], 11)
        self.assertEqual(usage['output'], 22)
        self.assertEqual(usage['cache_read'], 1300)
        self.assertEqual(usage['cache_write'], 9)
        self.assertEqual(usage['total'], 11 + 22 + 1300 + 9)
        self.assertEqual(usage['turns'], 2)

    def test_a_message_written_as_several_lines_is_counted_once(self) -> None:
        # Claude Code logs one line per content block and repeats the whole
        # usage on each — 7,169 lines for 3,026 messages in a real transcript.
        same = _usage(6613, 444, 26333, 11326)
        self._write(_line('m1', same), _line('m1', same), _line('m1', same))
        usage = self.reader.read(self.path)
        self.assertEqual(usage['turns'], 1)
        self.assertEqual(usage['output'], 444)
        self.assertEqual(usage['total'], 6613 + 444 + 26333 + 11326)

    def test_a_streamed_message_keeps_its_final_larger_figure(self) -> None:
        self._write(_line('m1', _usage(5, 1, 100, 0)), _line('m1', _usage(5, 80, 100, 0)))
        usage = self.reader.read(self.path)
        self.assertEqual(usage['output'], 80)
        self.assertEqual(usage['turns'], 1)

    def test_messages_without_an_id_are_each_counted(self) -> None:
        no_id = json.dumps({'type': 'assistant', 'message': {
            'model': 'claude-opus-5', 'usage': _usage(0, 7, 0, 0),
        }}) + '\n'
        self._write(no_id, no_id)
        usage = self.reader.read(self.path)
        self.assertEqual(usage['turns'], 2)
        self.assertEqual(usage['output'], 14)

    def test_splits_usage_by_model(self) -> None:
        self._write(
            _line('m1', _usage(0, 10, 0, 0), model='claude-opus-5'),
            _line('m2', _usage(0, 4, 0, 0), model='claude-sonnet-5'),
            _line('m3', _usage(0, 6, 0, 0), model='claude-opus-5'),
        )
        models = self.reader.read(self.path)['models']
        self.assertEqual(models['claude-opus-5']['output'], 16)
        self.assertEqual(models['claude-opus-5']['turns'], 2)
        self.assertEqual(models['claude-sonnet-5']['total'], 4)

    def test_tracks_the_first_and_last_message_time(self) -> None:
        self._write(
            _line('m2', _usage(0, 1, 0, 0), at='2026-10-05T00:00:00Z'),
            _line('m1', _usage(0, 1, 0, 0), at='2026-10-01T00:00:00Z'),
            _line('m3', _usage(0, 1, 0, 0), at='2026-10-09T00:00:00Z'),
        )
        usage = self.reader.read(self.path)
        self.assertEqual(usage['first_at'], '2026-10-01T00:00:00Z')
        self.assertEqual(usage['last_at'], '2026-10-09T00:00:00Z')

    # ----- what is NOT counted -----

    def test_ignores_everything_that_is_not_an_api_message(self) -> None:
        self._write(
            _line('u1', _usage(0, 99, 0, 0), kind='user'),
            json.dumps({'type': 'assistant', 'message': {'id': 'x', 'model': 'm'}}) + '\n',
            '{"type": "assistant", "message": ["usage"]}\n',  # a message that is no dict
            json.dumps({'type': 'assistant', 'message': {'id': 'y', 'usage': 'nope'}}) + '\n',
            json.dumps(['a', 'list', '"usage"']) + '\n',
            'this "usage" line is not json\n',
            json.dumps({'type': 'last-prompt'}) + '\n',
            '\n',
            _line('real', _usage(0, 3, 0, 0)),
        )
        usage = self.reader.read(self.path)
        self.assertEqual(usage['turns'], 1)
        self.assertEqual(usage['output'], 3)

    def test_synthetic_turns_are_not_api_calls(self) -> None:
        self._write(
            _line('s1', _usage(1, 1, 1, 1), model='<synthetic>'),
            _line('m1', _usage(0, 2, 0, 0)),
        )
        usage = self.reader.read(self.path)
        self.assertEqual(usage['turns'], 1)
        self.assertNotIn('<synthetic>', usage['models'])

    def test_malformed_counters_never_inflate_the_total(self) -> None:
        self._write(_line('m1', {
            'input_tokens': True,            # a bool must not count as 1
            'output_tokens': -50,            # negatives cannot drag a total down
            'cache_read_input_tokens': 'x',  # not a number
            'cache_creation_input_tokens': 7.9,
        }))
        usage = self.reader.read(self.path)
        self.assertEqual((usage['input'], usage['output'], usage['cache_read']), (0, 0, 0))
        self.assertEqual(usage['cache_write'], 7)
        self.assertEqual(usage['total'], 7)

    # ----- reading again -----

    def test_a_second_read_of_a_grown_file_adds_only_the_new_messages(self) -> None:
        self._write(_line('m1', _usage(0, 10, 100, 0)))
        first = self.reader.read(self.path)
        self.assertEqual(first['output'], 10)

        self._append(_line('m2', _usage(0, 5, 50, 0)))
        second = self.reader.read(self.path)

        self.assertEqual(second['output'], 15)
        self.assertEqual(second['turns'], 2)
        # The incremental answer is the answer a fresh read would give.
        self.assertEqual(second, TranscriptUsageReader().read(self.path))

    def test_a_repeat_line_appended_later_does_not_double_count(self) -> None:
        self._write(_line('m1', _usage(0, 10, 100, 0)))
        self.reader.read(self.path)
        self._append(_line('m1', _usage(0, 10, 100, 0)))
        self.assertEqual(self.reader.read(self.path)['turns'], 1)

    def test_a_half_written_last_line_is_picked_up_once_it_is_whole(self) -> None:
        whole = _line('m2', _usage(0, 5, 0, 0))
        self._write(_line('m1', _usage(0, 10, 0, 0)))
        self._append(whole[:30])                       # the CLI is mid-write
        self.assertEqual(self.reader.read(self.path)['turns'], 1)

        self._append(whole[30:])
        usage = self.reader.read(self.path)
        self.assertEqual(usage['turns'], 2)
        self.assertEqual(usage['output'], 15)

    def test_an_unchanged_file_is_not_parsed_again(self) -> None:
        self._write(_line('m1', _usage(0, 10, 0, 0)))
        self.reader.read(self.path)
        state = next(iter(self.reader._files.values()))
        consumed = state.offset
        self.reader.read(self.path)
        self.assertIs(next(iter(self.reader._files.values())), state)
        self.assertEqual(state.offset, consumed)

    def test_a_replaced_file_starts_over_instead_of_trusting_old_numbers(self) -> None:
        self._write(_line('old', _usage(0, 100, 0, 0)))
        self.assertEqual(self.reader.read(self.path)['output'], 100)

        # A different file at the same path (new inode, different start).
        replacement = self.path.with_name('replacement.jsonl')
        replacement.write_text(_line('new', _usage(0, 7, 0, 0)), encoding='utf-8')
        os.replace(replacement, self.path)

        usage = self.reader.read(self.path)
        self.assertEqual(usage['output'], 7)
        self.assertEqual(usage['turns'], 1)

    def test_a_truncated_file_starts_over(self) -> None:
        self._write(_line('a', _usage(0, 100, 0, 0)), _line('b', _usage(0, 100, 0, 0)))
        self.assertEqual(self.reader.read(self.path)['output'], 200)
        self.path.write_text(_line('a', _usage(0, 100, 0, 0)), encoding='utf-8')
        self.assertEqual(self.reader.read(self.path)['output'], 100)

    def test_a_missing_file_is_zero_not_an_error(self) -> None:
        self.assertEqual(self.reader.read(self.path), empty_usage())
        # ... and a file that disappears after being read stops being counted.
        self._write(_line('m1', _usage(0, 10, 0, 0)))
        self.reader.read(self.path)
        self.path.unlink()
        self.assertEqual(self.reader.read(self.path), empty_usage())
        self.assertEqual(self.reader._files, {})

    # ----- combining a task's transcripts -----

    def test_add_usage_combines_several_transcripts(self) -> None:
        one = Path(self._tmp.name) / 'one.jsonl'
        two = Path(self._tmp.name) / 'two.jsonl'
        one.write_text(
            _line('a', _usage(1, 2, 3, 4), at='2026-10-03T00:00:00Z'), encoding='utf-8',
        )
        two.write_text(
            _line('b', _usage(10, 20, 30, 40), model='claude-sonnet-5',
                  at='2026-10-01T00:00:00Z')
            + _line('c', _usage(0, 1, 0, 0), at='2026-10-09T00:00:00Z'),
            encoding='utf-8',
        )
        combined = add_usage(empty_usage(), self.reader.read(one))
        add_usage(combined, self.reader.read(two))

        self.assertEqual(combined['input'], 11)
        self.assertEqual(combined['output'], 23)
        self.assertEqual(combined['turns'], 3)
        self.assertEqual(combined['first_at'], '2026-10-01T00:00:00Z')
        self.assertEqual(combined['last_at'], '2026-10-09T00:00:00Z')
        self.assertEqual(sorted(combined['models']), ['claude-opus-5', 'claude-sonnet-5'])
        self.assertEqual(combined['total'], sum(combined[name] for name in COUNT_NAMES))

    def test_add_usage_tolerates_a_blank_or_partial_record(self) -> None:
        into = empty_usage()
        add_usage(into, {})
        self.assertEqual(into, empty_usage())
        add_usage(into, {'output': 5, 'total': 5, 'turns': 1, 'last_at': '2026-10-02T00:00:00Z',
                         'models': {'m': {'output': 5, 'total': 5, 'turns': 1}}})
        self.assertEqual(into['output'], 5)
        # No first_at was supplied, so none is invented; last_at is taken.
        self.assertEqual(into['first_at'], '')
        self.assertEqual(into['last_at'], '2026-10-02T00:00:00Z')
        self.assertEqual(into['models']['m']['turns'], 1)

    # ----- what it cost -----

    def test_each_message_is_priced_at_its_own_models_rates(self) -> None:
        # Opus 5.5: $4 in / $20 out / $0.20 read.  Sonnet 5.5: $2 / $10 / $0.20.
        self._write(
            _line('a', _usage(1_000_000, 100_000, 5_000_000, 0), model='claude-opus-5-5'),
            _line('b', _usage(1_000_000, 100_000, 5_000_000, 0), model='claude-sonnet-5-5'),
        )
        usage = self.reader.read(self.path)
        opus = usage['models']['claude-opus-5-5']['cost']
        sonnet = usage['models']['claude-sonnet-5-5']['cost']
        self.assertEqual(opus, {'output': 2.0, 'fresh': 4.0, 'cache_read': 1.0, 'total': 7.0})
        self.assertEqual(sonnet, {'output': 1.0, 'fresh': 2.0, 'cache_read': 1.0, 'total': 4.0})
        self.assertEqual(usage['cost'], {'output': 3.0, 'fresh': 6.0, 'cache_read': 2.0, 'total': 11.0})
        self.assertEqual(usage['unpriced_models'], [])

    def test_a_model_switched_mid_task_is_split_by_who_answered(self) -> None:
        self._write(
            _line('a', _usage(0, 10, 100, 0), model='claude-opus-5-5', at='2026-10-01T10:00:00Z'),
            _line('b', _usage(0, 20, 200, 0), model='claude-opus-5-5', at='2026-10-01T11:00:00Z'),
            _line('c', _usage(0, 30, 300, 0), model='claude-sonnet-5-5', at='2026-10-01T12:00:00Z'),
            _line('d', _usage(0, 40, 400, 0), model='claude-sonnet-5-5', at='2026-10-02T09:00:00Z'),
        )
        usage = self.reader.read(self.path)
        opus = usage['models']['claude-opus-5-5']
        sonnet = usage['models']['claude-sonnet-5-5']
        self.assertEqual((opus['turns'], opus['output']), (2, 30))
        self.assertEqual((sonnet['turns'], sonnet['output']), (2, 70))
        self.assertEqual((opus['first_at'], opus['last_at']),
                         ('2026-10-01T10:00:00Z', '2026-10-01T11:00:00Z'))
        self.assertEqual((sonnet['first_at'], sonnet['last_at']),
                         ('2026-10-01T12:00:00Z', '2026-10-02T09:00:00Z'))
        self.assertEqual(usage['turns'], 4)
        self.assertTrue(opus['priced'] and sonnet['priced'])

    def test_a_1h_cache_write_is_priced_at_twice_input_and_counted_once(self) -> None:
        usage_block = {**_usage(0, 0, 0, 1_000_000),
                       'cache_creation': {'ephemeral_1h_input_tokens': 400_000,
                                          'ephemeral_5m_input_tokens': 600_000}}
        self._write(_line('a', usage_block, model='claude-opus-5-5'))
        usage = self.reader.read(self.path)
        # 600k x 1.25 x $4 + 400k x 2 x $4 per million.
        self.assertAlmostEqual(usage['cost']['fresh'], 6.2)
        # The 1h tokens are PART of cache_write, never added on top of it.
        self.assertEqual(usage['cache_write'], 1_000_000)
        self.assertEqual(usage['total'], 1_000_000)

    def test_a_repeated_line_keeps_the_final_1h_figure(self) -> None:
        early = {**_usage(0, 0, 0, 500_000), 'cache_creation': {'ephemeral_1h_input_tokens': 100_000}}
        final = {**_usage(0, 0, 0, 500_000), 'cache_creation': {'ephemeral_1h_input_tokens': 500_000}}
        self._write(
            _line('a', early, model='claude-opus-5-5'),
            _line('a', final, model='claude-opus-5-5'),
        )
        usage = self.reader.read(self.path)
        self.assertEqual(usage['turns'], 1)
        self.assertAlmostEqual(usage['cost']['fresh'], 500_000 * 2 * 4.0 / 1_000_000)

    def test_a_model_with_no_price_is_listed_and_adds_nothing_to_the_cost(self) -> None:
        self._write(
            _line('a', _usage(0, 1_000_000, 0, 0), model='claude-opus-5-5'),
            _line('b', _usage(0, 1_000_000, 0, 0), model='claude-made-up-9'),
        )
        usage = self.reader.read(self.path)
        self.assertEqual(usage['unpriced_models'], ['claude-made-up-9'])
        self.assertFalse(usage['models']['claude-made-up-9']['priced'])
        self.assertEqual(usage['models']['claude-made-up-9']['cost']['total'], 0.0)
        self.assertEqual(usage['cost']['total'], 20.0)
        # Its tokens are still counted — only its price is unknown.
        self.assertEqual(usage['output'], 2_000_000)

    def test_haiku_5_5_is_priced_per_message_not_from_the_totals(self) -> None:
        # One small prompt on the cheap card, one over 100K on the dear card.
        # Priced from the sums (110,000 input in total) both would be dear.
        self._write(
            _line('small', _usage(50_000, 0, 0, 0), model='claude-haiku-5-5'),
            _line('large', _usage(60_000, 0, 0, 0), model='claude-haiku-5-5'),
        )
        self.assertAlmostEqual(self.reader.read(self.path)['cost']['fresh'],
                               50_000 * 0.10 / 1_000_000 + 60_000 * 0.10 / 1_000_000)
        # A transcript only ever grows, so a different file needs a reader of
        # its own rather than a rewrite under the first one's remembered offset.
        self._write(
            _line('small', _usage(50_000, 0, 0, 0), model='claude-haiku-5-5'),
            _line('large', _usage(110_000, 0, 0, 0), model='claude-haiku-5-5'),
        )
        self.assertAlmostEqual(TranscriptUsageReader().read(self.path)['cost']['fresh'],
                               50_000 * 0.10 / 1_000_000 + 110_000 * 0.50 / 1_000_000)

    def test_add_usage_merges_cost_unpriced_models_and_each_models_span(self) -> None:
        one = Path(self._tmp.name) / 'one.jsonl'
        two = Path(self._tmp.name) / 'two.jsonl'
        one.write_text(
            _line('a', _usage(0, 1_000_000, 0, 0), model='claude-opus-5-5',
                  at='2026-10-03T00:00:00Z')
            + _line('x', _usage(0, 5, 0, 0), model='claude-made-up-9', at='2026-10-03T00:00:00Z'),
            encoding='utf-8',
        )
        two.write_text(
            _line('b', _usage(0, 1_000_000, 0, 0), model='claude-opus-5-5',
                  at='2026-10-01T00:00:00Z')
            + _line('y', _usage(0, 5, 0, 0), model='claude-other-model', at='2026-10-01T00:00:00Z'),
            encoding='utf-8',
        )
        combined = add_usage(empty_usage(), self.reader.read(one))
        add_usage(combined, self.reader.read(two))

        self.assertEqual(combined['cost']['output'], 40.0)
        self.assertEqual(combined['cost']['total'], 40.0)
        self.assertEqual(combined['unpriced_models'], ['claude-made-up-9', 'claude-other-model'])
        opus = combined['models']['claude-opus-5-5']
        self.assertEqual(opus['cost']['total'], 40.0)
        self.assertEqual(opus['turns'], 2)
        self.assertEqual((opus['first_at'], opus['last_at']),
                         ('2026-10-01T00:00:00Z', '2026-10-03T00:00:00Z'))
        self.assertTrue(opus['priced'])

    def test_add_usage_still_accepts_a_record_from_before_cost_existed(self) -> None:
        into = empty_usage()
        add_usage(into, {'output': 5, 'total': 5, 'turns': 1,
                         'models': {'claude-opus-5-5': {'output': 5, 'total': 5, 'turns': 1}}})
        self.assertEqual(into['cost']['total'], 0.0)
        self.assertEqual(into['unpriced_models'], [])
        self.assertTrue(into['models']['claude-opus-5-5']['priced'])
        self.assertEqual(into['models']['claude-opus-5-5']['first_at'], '')

    def test_a_fresh_totals_record_carries_no_cost_and_no_unpriced_models(self) -> None:
        usage = empty_usage()
        self.assertEqual(usage['cost']['total'], 0.0)
        self.assertEqual(usage['unpriced_models'], [])
        self.assertEqual(self.reader.read(Path(self._tmp.name) / 'missing.jsonl'), empty_usage())


if __name__ == '__main__':
    unittest.main()
