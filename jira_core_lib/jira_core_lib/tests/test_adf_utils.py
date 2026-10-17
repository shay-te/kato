import unittest

from jira_core_lib.jira_core_lib.client.jira_client import JiraClient
from jira_core_lib.jira_core_lib.helpers.adf_utils import (
    adf_code_block,
    adf_document,
    adf_from_text,
    adf_paragraphs,
)


def _paragraph(text: str) -> dict:
    return {'type': 'paragraph', 'content': [{'type': 'text', 'text': text}]}


class AdfDocumentTests(unittest.TestCase):
    def test_wraps_the_blocks_in_order_in_a_versioned_doc(self) -> None:
        self.assertEqual(
            adf_document(_paragraph('a'), _paragraph('b')),
            {'type': 'doc', 'version': 1, 'content': [_paragraph('a'), _paragraph('b')]},
        )

    def test_an_empty_document_has_no_content(self) -> None:
        self.assertEqual(adf_document(), {'type': 'doc', 'version': 1, 'content': []})


class AdfParagraphsTests(unittest.TestCase):
    def test_one_paragraph_per_line(self) -> None:
        self.assertEqual(
            adf_paragraphs('First line.\nSecond line.'),
            [_paragraph('First line.'), _paragraph('Second line.')],
        )

    def test_blank_lines_make_no_node(self) -> None:
        # ADF rejects an empty text node; paragraphs already render apart.
        self.assertEqual(
            adf_paragraphs('\nFirst.\n\n   \nSecond.\n'),
            [_paragraph('First.'), _paragraph('Second.')],
        )

    def test_windows_line_endings_split_the_same_way(self) -> None:
        self.assertEqual(adf_paragraphs('a\r\nb'), [_paragraph('a'), _paragraph('b')])

    def test_a_line_keeps_its_own_text(self) -> None:
        self.assertEqual(adf_paragraphs('  - indented item'), [_paragraph('  - indented item')])

    def test_no_text_is_no_paragraphs(self) -> None:
        self.assertEqual(adf_paragraphs(''), [])
        self.assertEqual(adf_paragraphs(None), [])


class AdfCodeBlockTests(unittest.TestCase):
    def test_holds_the_text_byte_for_byte(self) -> None:
        code = '    indented\n\n{"a": 1}\n  trailing\n'
        self.assertEqual(
            adf_code_block(code),
            {'type': 'codeBlock', 'content': [{'type': 'text', 'text': code}]},
        )

    def test_no_text_is_an_empty_block(self) -> None:
        # ADF rejects an empty text node, so the block holds none.
        self.assertEqual(adf_code_block(''), {'type': 'codeBlock', 'content': []})
        self.assertEqual(adf_code_block(None), {'type': 'codeBlock', 'content': []})


class AdfFromTextTests(unittest.TestCase):
    def test_a_plain_text_body_is_a_document_of_paragraphs(self) -> None:
        self.assertEqual(
            adf_from_text('Ready.\nPlease review.'),
            {'type': 'doc', 'version': 1, 'content': [_paragraph('Ready.'), _paragraph('Please review.')]},
        )

    def test_reads_back_as_the_same_lines(self) -> None:
        self.assertEqual(
            JiraClient._adf_to_text(adf_from_text('Ready.\nPlease review.')), 'Ready.\nPlease review.',
        )

    def test_a_code_block_reads_back_exactly(self) -> None:
        code = '\n  first\n\n  second\n'
        document = adf_document(*adf_paragraphs('Intro'), adf_code_block(code))
        self.assertEqual(JiraClient._adf_to_text(document), 'Intro\n' + code)


if __name__ == '__main__':
    unittest.main()
