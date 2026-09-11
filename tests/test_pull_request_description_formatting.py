"""The PR description kato writes has to READ as structured text.

Reported as "the description kato write in the task dose not look good, make
sure it's formated properly", with a Bitbucket PR whose whole body rendered as
one wall of prose — every "- path/file.py did X" bullet running inline after
the "Files changed:" line that introduced them.

The newlines were there. Bitbucket's Markdown renderer needs a BLANK line
before a list, and the agent (reasonably) writes the tight form.
"""

from __future__ import annotations

import unittest

from kato_core_lib.helpers.pull_request_utils import markdown_blocks


class MarkdownBlocksTests(unittest.TestCase):
    def test_a_list_after_a_label_gets_its_blank_line(self) -> None:
        out = markdown_blocks('Files changed:\n- a.py did X\n- b.py did Y')
        self.assertEqual(out, 'Files changed:\n\n- a.py did X\n- b.py did Y')

    def test_items_within_a_list_stay_tight(self) -> None:
        # Blank lines BETWEEN items make a "loose" list, which renders with
        # extra spacing — worse to read, not better.
        out = markdown_blocks('- one\n- two\n- three')
        self.assertEqual(out, '- one\n- two\n- three')

    def test_numbered_lists_and_headings_too(self) -> None:
        self.assertIn('Steps:\n\n1. first', markdown_blocks('Steps:\n1. first'))
        self.assertIn('intro\n\n## Section', markdown_blocks('intro\n## Section'))

    def test_already_spaced_text_is_left_alone(self) -> None:
        text = 'Files changed:\n\n- a.py\n- b.py'
        self.assertEqual(markdown_blocks(text), text)

    def test_fenced_code_is_never_reflowed(self) -> None:
        # A ``#`` comment inside a shell block is not a heading, and a ``-``
        # is not a bullet. Spacing them out corrupts the snippet.
        text = 'Run:\n\n```bash\n# comment\n- not a bullet\n```'
        self.assertEqual(markdown_blocks(text), text)

    def test_runs_of_blank_lines_are_collapsed(self) -> None:
        self.assertEqual(markdown_blocks('a\n\n\n\n\nb'), 'a\n\nb')

    def test_empty_input_is_empty_output(self) -> None:
        self.assertEqual(markdown_blocks(''), '')
        self.assertEqual(markdown_blocks(None), '')

    def test_the_reported_shape_end_to_end(self) -> None:
        # Close to the real body: a label, a long bullet run, then prose.
        body = (
            'Files changed:\n'
            '- backend/user_profile_service.py get_profile_by_key now raises\n'
            '- backend/view_public_system.py Added @HandleException()\n'
            'Both covered by tests.'
        )
        out = markdown_blocks(body)
        lines = out.split('\n')
        # The label is its own paragraph...
        self.assertEqual(lines[0], 'Files changed:')
        self.assertEqual(lines[1], '')
        # ...the bullets are a tight list...
        self.assertTrue(lines[2].startswith('- '))
        self.assertTrue(lines[3].startswith('- '))
        # ...and nothing was lost.
        self.assertIn('Both covered by tests.', out)


if __name__ == '__main__':
    unittest.main()
