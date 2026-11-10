"""The conventions-aware commit message / PR title / PR description writer.

A model call that fails, times out, or answers nonsense must never block a
publish: kato's own plain text is the result then. Same shape as
test_release_note_writer.py.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from kato_core_lib.data_layers.service.publish_conventions_writer import (
    COMMIT_MARKER,
    DESCRIPTION_MARKER,
    TITLE_MARKER,
    PublishConventionsWriter,
    PublishText,
    build_prompt,
    parse_response,
)

CONTEXT = 'UNA-1: Add retry to the export job\n\nRetry 3 times with backoff.'
CONVENTIONS = 'Commits follow Conventional Commits: "feat: ...", "fix: ...".'


class PublishConventionsWriterTests(unittest.TestCase):
    def _writer(self, one_shot):
        return PublishConventionsWriter(one_shot=one_shot, logger=MagicMock())

    def test_a_full_response_is_used_for_commit_title_and_description(self) -> None:
        reply = (
            f'{COMMIT_MARKER}\nfeat: add retry\n'
            f'{TITLE_MARKER}\nUNA-1 Add retry\n'
            f'{DESCRIPTION_MARKER}\nRetries the export job 3 times.\n'
        )
        written = self._writer(lambda prompt: reply).write(
            context_text=CONTEXT, conventions_text=CONVENTIONS,
            fallback_commit_message='Implement UNA-1',
            fallback_title='UNA-1 fallback title', fallback_description='fallback description',
        )
        self.assertEqual(written, PublishText(
            commit_message='feat: add retry', title='UNA-1 Add retry',
            description='Retries the export job 3 times.',
        ))

    def test_commit_message_only_when_no_title_description_fallback_given(self) -> None:
        written = self._writer(lambda prompt: f'{COMMIT_MARKER}\nfeat: x\n').write(
            context_text=CONTEXT, conventions_text=CONVENTIONS,
            fallback_commit_message='Address review comments',
        )
        self.assertEqual(written, PublishText(commit_message='feat: x', title='', description=''))

    def test_a_response_missing_a_section_falls_back_for_that_section_only(self) -> None:
        # Only the commit message came back — title/description still fall
        # back, never discarded wholesale with the rest of a good response.
        written = self._writer(lambda prompt: f'{COMMIT_MARKER}\nfeat: x\n').write(
            context_text=CONTEXT, conventions_text=CONVENTIONS,
            fallback_commit_message='Implement UNA-1',
            fallback_title='fallback title', fallback_description='fallback description',
        )
        self.assertEqual(written, PublishText(
            commit_message='feat: x', title='fallback title', description='fallback description',
        ))

    def test_a_failing_model_call_falls_back_entirely(self) -> None:
        def boom(prompt):
            raise RuntimeError('claude timed out')

        writer = self._writer(boom)
        written = writer.write(
            context_text=CONTEXT, conventions_text=CONVENTIONS,
            fallback_commit_message='Implement UNA-1',
            fallback_title='fallback title', fallback_description='fallback description',
        )
        self.assertEqual(written, PublishText('Implement UNA-1', 'fallback title', 'fallback description'))
        writer.logger.exception.assert_called_once()

    def test_an_empty_answer_falls_back_entirely(self) -> None:
        written = self._writer(lambda prompt: '  \n ').write(
            context_text=CONTEXT, conventions_text=CONVENTIONS,
            fallback_commit_message='Implement UNA-1',
        )
        self.assertEqual(written, PublishText('Implement UNA-1'))

    def test_no_conventions_text_skips_the_call_entirely(self) -> None:
        one_shot = MagicMock()
        written = self._writer(one_shot).write(
            context_text=CONTEXT, conventions_text='   ',
            fallback_commit_message='Implement UNA-1',
        )
        self.assertEqual(written, PublishText('Implement UNA-1'))
        one_shot.assert_not_called()

    def test_no_model_wired_skips_the_call_entirely(self) -> None:
        written = PublishConventionsWriter(one_shot=None).write(
            context_text=CONTEXT, conventions_text=CONVENTIONS,
            fallback_commit_message='Implement UNA-1',
        )
        self.assertEqual(written, PublishText('Implement UNA-1'))


class BuildPromptTests(unittest.TestCase):
    def test_context_and_conventions_are_framed_as_untrusted_data(self) -> None:
        prompt = build_prompt(
            context_text=CONTEXT, conventions_text=CONVENTIONS,
            fallback_commit_message='Implement UNA-1',
        )
        self.assertIn('untrusted', prompt.lower())
        self.assertIn('Add retry to the export job', prompt)
        self.assertIn('Conventional Commits', prompt)
        self.assertIn('not instructions', prompt)

    def test_asks_only_for_the_commit_message_with_no_title_description_fallback(self) -> None:
        prompt = build_prompt(
            context_text=CONTEXT, conventions_text=CONVENTIONS,
            fallback_commit_message='Implement UNA-1',
        )
        self.assertIn(COMMIT_MARKER, prompt)
        self.assertNotIn(TITLE_MARKER, prompt)
        self.assertNotIn(DESCRIPTION_MARKER, prompt)

    def test_asks_for_all_three_when_title_and_description_fallbacks_are_given(self) -> None:
        prompt = build_prompt(
            context_text=CONTEXT, conventions_text=CONVENTIONS,
            fallback_commit_message='Implement UNA-1',
            fallback_title='t', fallback_description='d',
        )
        self.assertIn(COMMIT_MARKER, prompt)
        self.assertIn(TITLE_MARKER, prompt)
        self.assertIn(DESCRIPTION_MARKER, prompt)

    def test_a_huge_conventions_file_is_trimmed(self) -> None:
        prompt = build_prompt(
            context_text=CONTEXT, conventions_text='x' * 50_000,
            fallback_commit_message='Implement UNA-1',
        )
        self.assertLess(len(prompt), 15_000)

    def test_tells_the_model_to_keep_the_defaults_when_nothing_applies(self) -> None:
        prompt = build_prompt(
            context_text=CONTEXT, conventions_text=CONVENTIONS,
            fallback_commit_message='Implement UNA-1',
        )
        self.assertIn('UNCHANGED', prompt)


class ParseResponseTests(unittest.TestCase):
    def test_markers_are_matched_exactly_not_as_substrings(self) -> None:
        # A line that merely CONTAINS a marker word must not be mistaken
        # for the marker itself.
        raw = f'Some text mentions {COMMIT_MARKER} inline\n{COMMIT_MARKER}\nfeat: real one\n'
        written = parse_response(raw, fallback_commit_message='fallback')
        self.assertEqual(written.commit_message, 'feat: real one')

    def test_garbage_with_no_markers_falls_back_entirely(self) -> None:
        written = parse_response(
            'nothing useful here', fallback_commit_message='fallback',
            fallback_title='t', fallback_description='d',
        )
        self.assertEqual(written, PublishText('fallback', 't', 'd'))

    def test_a_multi_line_description_is_kept_whole(self) -> None:
        raw = f'{DESCRIPTION_MARKER}\nline one\nline two\n\nline four\n'
        written = parse_response(raw, fallback_commit_message='c', fallback_description='d')
        self.assertEqual(written.description, 'line one\nline two\n\nline four')


if __name__ == '__main__':
    unittest.main()
