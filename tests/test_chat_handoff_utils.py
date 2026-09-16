"""A new chat starts from the summary the old one's agent wrote.

Asked for: "when this is very close to finishing or maybe when I click on this I
want him to show me an option to launch a new chat with the summary of the
previous chat so the new chat is loaded with the context from this previous
chat". The agent writes the summary between markers; kato lifts it out of the
chat's events — live or on disk — and it becomes the new chat's first message.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from kato_core_lib.helpers.chat_handoff_utils import (
    HANDOFF_CLOSE,
    HANDOFF_OPEN,
    handoff_summary_from_events,
    handoff_summary_from_texts,
    new_chat_opening_message,
)

PROMPT = (
    Path(__file__).resolve().parents[1]
    / 'webserver' / 'ui' / 'src' / 'predefined_prompts' / 'handoff_summary.md'
)


def _block(summary: str) -> str:
    return f'{HANDOFF_OPEN}\n{summary}\n{HANDOFF_CLOSE}'


def _assistant(text: str):
    return SimpleNamespace(
        event_type='assistant',
        raw={'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': text}]}},
    )


def _user(text: str):
    return SimpleNamespace(
        event_type='user',
        raw={'type': 'user', 'message': {'content': text}},
    )


class HandoffSummaryFromTextsTests(unittest.TestCase):
    def test_the_block_is_lifted_out_and_trimmed(self) -> None:
        self.assertEqual(
            handoff_summary_from_texts([f'Here it is:\n{_block("  Goal: ship it  ")}\nDone.']),
            'Goal: ship it',
        )

    def test_the_newest_text_with_a_block_wins(self) -> None:
        self.assertEqual(
            handoff_summary_from_texts([_block('old'), 'unrelated', _block('new')]),
            'new',
        )

    def test_a_later_text_without_a_block_does_not_hide_the_summary(self) -> None:
        self.assertEqual(
            handoff_summary_from_texts([_block('summary'), 'Anything else?']),
            'summary',
        )

    def test_the_last_block_in_one_text_wins(self) -> None:
        self.assertEqual(
            handoff_summary_from_texts([f'{_block("draft")} {_block("final")}']),
            'final',
        )

    def test_a_blank_block_does_not_hide_a_real_one(self) -> None:
        self.assertEqual(
            handoff_summary_from_texts([_block('the summary'), f'{_block("real")} {_block("  ")}']),
            'real',
        )
        self.assertEqual(
            handoff_summary_from_texts([_block('the summary'), _block('  ')]),
            'the summary',
        )

    def test_a_blank_or_unclosed_block_is_no_summary(self) -> None:
        for texts in ([_block('   ')], [f'{HANDOFF_OPEN} never closed'], ['plain text'], [], None):
            with self.subTest(texts=texts):
                self.assertEqual(handoff_summary_from_texts(texts), '')


class HandoffSummaryFromEventsTests(unittest.TestCase):
    def test_live_session_events(self) -> None:
        events = [_user('summarise please'), _assistant(_block('the summary'))]
        self.assertEqual(handoff_summary_from_events(events), 'the summary')

    def test_transcript_records_read_from_disk(self) -> None:
        records = [event.raw for event in (_user('go'), _assistant(_block('from disk')))]
        self.assertEqual(handoff_summary_from_events(records), 'from disk')

    def test_the_prompt_quoting_the_markers_is_not_the_summary(self) -> None:
        # The request itself spells the markers out; only the AGENT's text counts.
        events = [_user(f'Put it between:\n{_block("(the summary goes here)")}')]
        self.assertEqual(handoff_summary_from_events(events), '')

    def test_junk_events_are_skipped(self) -> None:
        self.assertEqual(handoff_summary_from_events([None, 'x', 3]), '')
        self.assertEqual(handoff_summary_from_events(None), '')


class OpeningMessageTests(unittest.TestCase):
    def test_carries_the_summary_after_saying_what_it_is(self) -> None:
        message = new_chat_opening_message('  Goal: ship it  ')
        self.assertTrue(message.endswith('Goal: ship it'))
        self.assertIn('handoff summary', message)
        self.assertIn('confirm the current state of the files', message)


class PromptMarkersContractTests(unittest.TestCase):
    def test_the_ui_prompt_asks_for_exactly_these_markers(self) -> None:
        # The prompt ships in the UI; the extraction lives here. A marker
        # spelled differently in one of them means every handoff 409s.
        text = PROMPT.read_text(encoding='utf-8')
        self.assertIn(HANDOFF_OPEN, text)
        self.assertIn(HANDOFF_CLOSE, text)


if __name__ == '__main__':
    unittest.main()
