"""Coverage for the untrusted-name → safe-file helpers.

The security surface is the FILENAME: it arrives from outside (an operator
upload, or an issue-tracker attachment), is used as a path segment, and is
echoed back into a prompt.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from utils_core_lib.utils_core_lib.filename_utils import (
    safe_attachment_name,
    unique_file_path,
)


class SafeAttachmentNameTests(unittest.TestCase):

    def test_keeps_an_ordinary_name(self) -> None:
        self.assertEqual(safe_attachment_name('messaging_js_logs.txt'),
                         'messaging_js_logs.txt')

    def test_keeps_an_ordinary_image_name(self) -> None:
        self.assertEqual(safe_attachment_name('bug.png'), 'bug.png')

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

    def test_a_name_of_only_unsafe_characters_becomes_a_placeholder(self) -> None:
        self.assertEqual(safe_attachment_name('///'), 'attachment.txt')
        self.assertEqual(safe_attachment_name('!!!'), 'attachment.txt')

    def test_very_long_name_keeps_its_extension(self) -> None:
        name = safe_attachment_name('a' * 400 + '.txt')
        self.assertLessEqual(len(name), 120)
        self.assertTrue(name.endswith('.txt'))


class UniqueFilePathTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name)

    def test_a_free_name_is_used_as_is(self) -> None:
        self.assertEqual(
            unique_file_path(self.directory, 'bug.png'),
            self.directory / 'bug.png',
        )

    def test_a_taken_name_is_suffixed_and_keeps_its_extension(self) -> None:
        (self.directory / 'bug.png').write_bytes(b'first')
        self.assertEqual(
            unique_file_path(self.directory, 'bug.png'),
            self.directory / 'bug-2.png',
        )

    def test_suffixes_keep_climbing(self) -> None:
        for name in ('bug.png', 'bug-2.png', 'bug-3.png'):
            (self.directory / name).write_bytes(b'x')
        self.assertEqual(
            unique_file_path(self.directory, 'bug.png'),
            self.directory / 'bug-4.png',
        )

    def test_the_original_file_is_never_overwritten(self) -> None:
        (self.directory / 'bug.png').write_bytes(b'first')
        unique_file_path(self.directory, 'bug.png').write_bytes(b'second')
        self.assertEqual((self.directory / 'bug.png').read_bytes(), b'first')

    def test_an_extensionless_name_still_gets_a_suffix(self) -> None:
        (self.directory / 'notes').write_bytes(b'x')
        self.assertEqual(
            unique_file_path(self.directory, 'notes'),
            self.directory / 'notes-2',
        )

    def test_an_exhausted_range_falls_back_to_the_process_id(self) -> None:
        # Pathological, but it must still return a path rather than loop or
        # hand back one that would clobber an existing file.
        for index in range(2, 1000):
            (self.directory / f'bug-{index}.png').write_bytes(b'x')
        (self.directory / 'bug.png').write_bytes(b'x')
        self.assertEqual(
            unique_file_path(self.directory, 'bug.png'),
            self.directory / f'bug-{os.getpid()}.png',
        )

    def test_a_string_directory_is_accepted(self) -> None:
        self.assertEqual(
            unique_file_path(str(self.directory), 'bug.png'),
            self.directory / 'bug.png',
        )


if __name__ == '__main__':
    unittest.main()
