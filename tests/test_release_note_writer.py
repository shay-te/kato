"""The release-note writer: one clean line from a finished task, or its title.

A model call that fails, times out or answers nonsense must never lose the
note: the ticket title is the line then. What the model reads came from a
ticket and the agent's workspace, so it is framed as untrusted data.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from kato_core_lib.data_layers.data.release_note_source import ReleaseNoteSource
from kato_core_lib.data_layers.service.release_note_writer import ReleaseNoteWriter, build_prompt

SOURCE = ReleaseNoteSource(
    task_id='UNA-1', title='Set a password button',
    description='Add a button on the client profile.', pr_description='Adds POST /set-password.',
    repositories=('backend',),
)


class ReleaseNoteWriterTests(unittest.TestCase):
    def _writer(self, one_shot):
        return ReleaseNoteWriter(one_shot=one_shot, logger=MagicMock())

    def test_the_model_s_line_is_used_cleaned(self) -> None:
        line = self._writer(lambda prompt: '## Clients can now set a password\nextra chatter').write(SOURCE)
        self.assertEqual(line, 'Clients can now set a password')

    def test_a_failing_model_call_falls_back_to_the_title(self) -> None:
        def boom(prompt):
            raise RuntimeError('claude timed out')

        writer = self._writer(boom)
        self.assertEqual(writer.write(SOURCE), 'Set a password button')
        writer.logger.exception.assert_called_once()

    def test_an_empty_answer_falls_back_to_the_title(self) -> None:
        self.assertEqual(self._writer(lambda prompt: '  \n ').write(SOURCE), 'Set a password button')

    def test_no_model_wired_means_the_title(self) -> None:
        self.assertEqual(ReleaseNoteWriter(one_shot=None).write(SOURCE), 'Set a password button')

    def test_no_title_either_means_the_task_id(self) -> None:
        source = ReleaseNoteSource(task_id='UNA-2')
        self.assertEqual(ReleaseNoteWriter(one_shot=None).write(source), 'UNA-2')


class BuildPromptTests(unittest.TestCase):
    def test_the_task_text_is_framed_as_untrusted_data(self) -> None:
        prompt = build_prompt(SOURCE)
        self.assertIn('untrusted', prompt.lower())
        self.assertIn('Add a button on the client profile.', prompt)
        self.assertIn('Adds POST /set-password.', prompt)
        self.assertTrue(prompt.rstrip().endswith('Reply with the line only.'))

    def test_no_pr_description_means_no_empty_section(self) -> None:
        prompt = build_prompt(ReleaseNoteSource(task_id='UNA-1', title='T', description='D'))
        self.assertNotIn('written by the developer', prompt)

    def test_a_huge_description_is_trimmed(self) -> None:
        prompt = build_prompt(ReleaseNoteSource(task_id='UNA-1', title='T', description='x' * 50_000))
        self.assertLess(len(prompt), 10_000)


if __name__ == '__main__':
    unittest.main()
