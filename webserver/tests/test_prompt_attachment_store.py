"""Coverage for the large-file composer attachment store.

Small text files are inlined into the prompt. Past a threshold that only
wastes context and silently truncates the interesting part, so the file is
written into the task workspace and the prompt carries its path instead.

The security surface here is the FILENAME: it comes straight from an upload,
is used as a path segment, and is echoed back into the prompt.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kato_webserver.prompt_attachment_store import (
    ATTACHMENTS_DIRNAME,
    MAX_ATTACHMENT_BYTES,
    attachments_dir,
    safe_attachment_name,
    save_attachment,
)


class SafeAttachmentNameTests(unittest.TestCase):

    def test_keeps_an_ordinary_name(self) -> None:
        self.assertEqual(safe_attachment_name('messaging_js_logs.txt'),
                         'messaging_js_logs.txt')

    def test_strips_a_posix_path(self) -> None:
        self.assertEqual(safe_attachment_name('/etc/passwd'), 'passwd')

    def test_strips_posix_traversal(self) -> None:
        self.assertEqual(safe_attachment_name('../../etc/passwd'), 'passwd')

    def test_strips_windows_traversal(self) -> None:
        # basename() alone does NOT save us here: on POSIX a backslash is an
        # ordinary character, so it would return the whole string untouched.
        self.assertEqual(
            safe_attachment_name(r'..\..\windows\system32\config'), 'config',
        )

    def test_dot_only_names_become_a_placeholder(self) -> None:
        # '.' and '..' resolve to directories, not files.
        for name in ('.', '..', '...'):
            self.assertEqual(safe_attachment_name(name), 'attachment.txt')

    def test_blank_becomes_a_placeholder(self) -> None:
        for name in ('', '   ', None):
            self.assertEqual(safe_attachment_name(name), 'attachment.txt')

    def test_unsafe_characters_collapse(self) -> None:
        self.assertEqual(safe_attachment_name('my logs (v2)!.txt'),
                         'my-logs-v2-.txt')

    def test_very_long_name_keeps_its_extension(self) -> None:
        name = safe_attachment_name('a' * 400 + '.txt')
        self.assertLessEqual(len(name), 120)
        self.assertTrue(name.endswith('.txt'))


class SaveAttachmentTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = self._tmp.name

    def test_writes_into_the_attachments_folder(self) -> None:
        result = save_attachment(self.workspace, 'logs.txt', b'hello')

        self.assertTrue(result['ok'])
        self.assertEqual(result['name'], 'logs.txt')
        self.assertEqual(result['bytes'], 5)
        written = Path(result['path'])
        self.assertEqual(written.read_bytes(), b'hello')
        # The task folder — the clone's PARENT — so git cannot stage it and
        # the agent's --add-dir scope still covers it.
        self.assertEqual(written.parent.name, ATTACHMENTS_DIRNAME)
        self.assertEqual(written.parent, attachments_dir(self.workspace))

    def test_traversal_cannot_escape_the_workspace(self) -> None:
        result = save_attachment(self.workspace, '../../escaped.txt', b'x')

        self.assertTrue(result['ok'])
        self.assertEqual(Path(result['path']).parent,
                         attachments_dir(self.workspace))

    def test_second_file_of_the_same_name_does_not_overwrite(self) -> None:
        # The prompt references a PATH — silently replacing the first file
        # would point it at contents the operator never attached.
        first = save_attachment(self.workspace, 'logs.txt', b'first')
        second = save_attachment(self.workspace, 'logs.txt', b'second')

        self.assertNotEqual(first['path'], second['path'])
        self.assertEqual(second['name'], 'logs-2.txt')
        self.assertEqual(Path(first['path']).read_bytes(), b'first')
        self.assertEqual(Path(second['path']).read_bytes(), b'second')

    def test_rejects_an_empty_file(self) -> None:
        result = save_attachment(self.workspace, 'empty.txt', b'')
        self.assertFalse(result['ok'])

    def test_rejects_a_file_over_the_cap(self) -> None:
        result = save_attachment(
            self.workspace, 'huge.bin', b'x' * (MAX_ATTACHMENT_BYTES + 1),
        )
        self.assertFalse(result['ok'])
        self.assertIn('larger than', result['error'])

    def test_missing_workspace_is_an_error_not_a_crash(self) -> None:
        result = save_attachment('', 'logs.txt', b'x')
        self.assertFalse(result['ok'])

    def test_unwritable_target_reports_instead_of_raising(self) -> None:
        # A file where the attachments DIRECTORY should go: mkdir fails, and
        # the composer must stay usable rather than see an exception.
        Path(self.workspace, ATTACHMENTS_DIRNAME).write_text('x')
        result = save_attachment(self.workspace, 'logs.txt', b'x')
        self.assertFalse(result['ok'])
        self.assertTrue(result['error'])


if __name__ == '__main__':
    unittest.main()
