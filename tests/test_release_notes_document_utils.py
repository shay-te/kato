"""The release-notes document: where an entry lands, and that it lands once.

Pure text in, text out. The document is read again on every write, so an entry
built from untrusted text (a model's line, a ticket title) must never be able
to forge a heading, a marker or a second line.
"""

from __future__ import annotations

import unittest

from kato_core_lib.helpers.release_notes_document_utils import (
    add_entry,
    clean_line,
    entry_marker,
    format_entry,
    has_entry,
    replace_entry_line,
)


def _entry(task_id='UNA-1', text='Clients can set a password', done_on='2026-10-21', links=()):
    return format_entry(task_id=task_id, text=text, done_on=done_on, links=links)


class FormatEntryTests(unittest.TestCase):
    def test_an_entry_leads_with_its_date_and_ends_with_its_marker(self) -> None:
        line = _entry(links=[('PR', 'https://git.example/pr/7')])
        self.assertEqual(
            line,
            '- 2026-10-21 — Clients can set a password (UNA-1) · [PR](https://git.example/pr/7) '
            '<!-- kato:task UNA-1 -->',
        )

    def test_a_date_in_the_text_never_takes_the_place_of_kato_s(self) -> None:
        line = _entry(text='2020-01-01 — shipped long ago', done_on='2026-10-21')
        self.assertTrue(line.startswith('- 2026-10-21 — '))

    def test_empty_text_falls_back_to_the_task_id(self) -> None:
        self.assertTrue(_entry(text='   ').startswith('- 2026-10-21 — UNA-1 (UNA-1)'))

    def test_a_link_that_is_not_a_plain_web_address_is_dropped(self) -> None:
        line = _entry(links=[('PR', 'javascript:alert(1)'), ('PR', 'https://x.example/a b')])
        self.assertNotIn('](', line)


class CleanLineTests(unittest.TestCase):
    def test_only_the_first_line_and_no_forged_heading_or_marker(self) -> None:
        self.assertEqual(clean_line('## Fixed\nsecond line'), 'Fixed')
        self.assertEqual(clean_line('Done <!-- kato:task UNA-9 --> really'), 'Done kato:task UNA-9 really')
        self.assertEqual(clean_line('> - * quoted item'), 'quoted item')
        self.assertEqual(clean_line(None), '')

    def test_a_long_line_is_capped(self) -> None:
        line = clean_line('x' * 500)
        self.assertEqual(len(line), 200)
        self.assertTrue(line.endswith('…'))


class AddEntryTests(unittest.TestCase):
    def test_an_empty_document_gets_a_title_unreleased_and_the_repository(self) -> None:
        doc = add_entry('', task_id='UNA-1', repositories=['backend'], line=_entry())
        self.assertEqual(doc, (
            '# Release notes\n\n## Unreleased\n\n### backend\n\n'
            '- 2026-10-21 — Clients can set a password (UNA-1) <!-- kato:task UNA-1 -->\n'
        ))

    def test_a_task_in_several_repositories_is_listed_under_each(self) -> None:
        doc = add_entry('', task_id='UNA-1', repositories=['backend', 'client'], line=_entry())
        self.assertEqual(doc.count('<!-- kato:task UNA-1 -->'), 2)
        self.assertLess(doc.index('### backend'), doc.index('### client'))

    def test_no_repository_goes_under_other(self) -> None:
        doc = add_entry('', task_id='UNA-1', repositories=[], line=_entry())
        self.assertIn('### Other', doc)

    def test_the_newest_entry_goes_first_under_its_heading(self) -> None:
        doc = add_entry('', task_id='UNA-1', repositories=['backend'], line=_entry())
        doc = add_entry(doc, task_id='UNA-2', repositories=['backend'],
                        line=_entry(task_id='UNA-2', text='Second'))
        self.assertLess(doc.index('UNA-2'), doc.index('UNA-1'))
        self.assertEqual(doc.count('### backend'), 1)

    def test_unreleased_goes_before_the_first_version_and_nothing_else_moves(self) -> None:
        released = (
            '# Release notes\n\nWhat changed, release by release.\n\n'
            '## 1.0.0\n\n### backend\n\n- 2026-09-01 — Old thing (UNA-0) <!-- kato:task UNA-0 -->\n'
        )
        doc = add_entry(released, task_id='UNA-1', repositories=['backend'], line=_entry())
        self.assertLess(doc.index('What changed'), doc.index('## Unreleased'))
        self.assertLess(doc.index('## Unreleased'), doc.index('## 1.0.0'))
        # The released section is byte-for-byte what it was.
        self.assertTrue(doc.endswith(released[released.index('## 1.0.0'):]))
        # The new entry is in Unreleased, not under 1.0.0's backend heading.
        self.assertLess(doc.index('UNA-1'), doc.index('## 1.0.0'))

    def test_an_existing_unreleased_section_and_heading_are_reused(self) -> None:
        doc = '# Notes\n\n## [Unreleased]\n\n### Backend\n\n- older <!-- kato:task UNA-0 -->\n\n## 1.0.0\n'
        out = add_entry(doc, task_id='UNA-1', repositories=['backend'], line=_entry())
        self.assertEqual(out.count('Unreleased'), 1)
        self.assertEqual(out.lower().count('### backend'), 1)
        self.assertLess(out.index('UNA-1'), out.index('UNA-0'))

    def test_a_new_repository_heading_stays_inside_unreleased(self) -> None:
        doc = '# Notes\n\n## Unreleased\n\n### backend\n\n- a <!-- kato:task UNA-0 -->\n\n## 1.0.0\n'
        out = add_entry(doc, task_id='UNA-1', repositories=['client'], line=_entry())
        self.assertLess(out.index('### client'), out.index('## 1.0.0'))
        self.assertIn('<!-- kato:task UNA-0 -->\n\n### client\n\n- 2026-10-21', out)
        self.assertIn('(UNA-1) <!-- kato:task UNA-1 -->\n\n## 1.0.0', out)

    def test_headings_inside_code_fences_are_text_not_structure(self) -> None:
        doc = '# Notes\n\n```\n## Unreleased\n### backend\n```\n'
        out = add_entry(doc, task_id='UNA-1', repositories=['backend'], line=_entry())
        self.assertIn('```\n## Unreleased\n### backend\n```', out)
        self.assertEqual(out.count('## Unreleased'), 2)  # the fenced one and the real one
        self.assertGreater(out.index('<!-- kato:task UNA-1 -->'), out.index('```\n', 15))

    def test_a_task_already_listed_anywhere_is_never_added_again(self) -> None:
        moved = '# Notes\n\n## 1.0.0\n\n### backend\n\n- shipped (UNA-1) <!-- kato:task UNA-1 -->\n'
        self.assertEqual(add_entry(moved, task_id='una-1', repositories=['x'], line=_entry()), moved)
        self.assertTrue(has_entry(moved, 'UNA-1'))
        self.assertFalse(has_entry(moved, 'UNA-10'))
        self.assertFalse(has_entry(moved, ''))

    def test_a_forged_marker_in_the_text_does_not_count_as_another_task(self) -> None:
        line = _entry(text='hi <!-- kato:task UNA-99 -->')
        doc = add_entry('', task_id='UNA-1', repositories=['backend'], line=line)
        self.assertFalse(has_entry(doc, 'UNA-99'))


class ReplaceEntryLineTests(unittest.TestCase):
    def test_the_line_is_swapped_wherever_it_is_unchanged(self) -> None:
        first = _entry(text='Title of the ticket')
        better = _entry(text='Clients can set a password')
        doc = add_entry('', task_id='UNA-1', repositories=['a', 'b'], line=first)
        out, count = replace_entry_line(doc, task_id='UNA-1', old=first, new=better)
        self.assertEqual(count, 2)
        self.assertNotIn('Title of the ticket', out)

    def test_an_operator_edit_is_never_overwritten(self) -> None:
        first = _entry(text='Title of the ticket')
        doc = add_entry('', task_id='UNA-1', repositories=['a'], line=first)
        edited = doc.replace('Title of the ticket', 'My own words')
        out, count = replace_entry_line(edited, task_id='UNA-1', old=first, new=_entry(text='AI'))
        self.assertEqual((out, count), (edited, 0))

    def test_another_task_s_identical_text_is_left_alone(self) -> None:
        line = _entry(task_id='UNA-2')
        doc = add_entry('', task_id='UNA-2', repositories=['a'], line=line)
        self.assertEqual(replace_entry_line(doc, task_id='UNA-1', old=line, new='x')[1], 0)

    def test_marker_text_helper(self) -> None:
        self.assertEqual(entry_marker(' UNA-1 '), '<!-- kato:task UNA-1 -->')


if __name__ == '__main__':
    unittest.main()
