"""A cached read is served again only while the file is provably unchanged."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from utils_core_lib.utils_core_lib.file_snapshot import (
    RACY_WINDOW_NS,
    FileSignature,
    file_signature,
    trustworthy,
)


class FileSignatureTests(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'store.json'

    def test_a_missing_file_has_no_signature(self) -> None:
        self.assertIsNone(file_signature(self.path))

    def test_it_is_the_time_the_size_and_the_inode(self) -> None:
        self.path.write_text('{"a": 1}', encoding='utf-8')
        stat = os.stat(self.path)
        self.assertEqual(
            file_signature(self.path),
            FileSignature(stat.st_mtime_ns, stat.st_size, stat.st_ino),
        )

    def test_a_replace_in_the_same_clock_tick_still_changes_it(self) -> None:
        # Linux stamps file times from a coarse clock, so two writes inside
        # one tick share st_mtime_ns. Forced here with utime.
        self.path.write_text('{"status": "queued"}', encoding='utf-8')
        before = file_signature(self.path)
        replacement = self.path.with_suffix('.tmp')
        replacement.write_text('{"status": "failed"}', encoding='utf-8')  # same size
        os.replace(replacement, self.path)
        os.utime(self.path, ns=(before.mtime_ns, before.mtime_ns))
        after = file_signature(self.path)
        self.assertEqual((after.mtime_ns, after.size), (before.mtime_ns, before.size))
        self.assertNotEqual(after, before)  # the inode gave it away


class TrustworthyTests(unittest.TestCase):

    def test_a_missing_file_can_be_cached(self) -> None:
        self.assertTrue(trustworthy(None))

    def test_a_file_still_inside_the_racy_window_cannot(self) -> None:
        now = 10 * RACY_WINDOW_NS
        signature = FileSignature(mtime_ns=now - RACY_WINDOW_NS + 1, size=1, inode=1)
        self.assertFalse(trustworthy(signature, now_ns=now))

    def test_a_file_quiet_for_the_whole_window_can(self) -> None:
        now = 10 * RACY_WINDOW_NS
        self.assertTrue(trustworthy(FileSignature(now - RACY_WINDOW_NS, 1, 1), now_ns=now))

    def test_a_time_in_the_future_is_not_trusted(self) -> None:
        now = 10 * RACY_WINDOW_NS
        self.assertFalse(trustworthy(FileSignature(now + 5, 1, 1), now_ns=now))

    def test_it_reads_the_clock_when_not_given_one(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'fresh.json'
        path.write_text('{}', encoding='utf-8')
        self.assertFalse(trustworthy(file_signature(path)))  # just written
        old = file_signature(path).mtime_ns - 2 * RACY_WINDOW_NS
        os.utime(path, ns=(old, old))
        self.assertTrue(trustworthy(file_signature(path)))


if __name__ == '__main__':
    unittest.main()
