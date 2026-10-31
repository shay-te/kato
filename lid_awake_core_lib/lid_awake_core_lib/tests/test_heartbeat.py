from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from lid_awake_core_lib.lid_awake_core_lib.heartbeat import Heartbeat


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'nested' / 'heartbeat'
        self.heartbeat = Heartbeat(self.path)

    def test_absent_until_the_first_beat(self):
        self.assertFalse(self.heartbeat.exists())
        self.heartbeat.beat()
        self.assertTrue(self.heartbeat.exists())

    def test_a_beat_makes_an_old_file_fresh(self):
        self.heartbeat.beat()
        os.utime(self.path, (1000, 1000))
        self.heartbeat.beat()
        self.assertGreater(self.path.stat().st_mtime, 1000)

    def test_clear_is_safe_twice(self):
        self.heartbeat.beat()
        self.heartbeat.clear()
        self.heartbeat.clear()
        self.assertFalse(self.heartbeat.exists())


if __name__ == '__main__':
    unittest.main()
