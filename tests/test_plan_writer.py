"""Tests for the Kato-specific plan.md atomic writer.

The generic plan extractor is tested in
``agent_core_lib/agent_core_lib/tests/test_plan_capture_utils.py``; this
file covers only the workspace-on-disk write path that stays in
``kato_core_lib``.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kato_core_lib.helpers.plan_writer import PLAN_FILENAME, write_plan


class WritePlanTests(unittest.TestCase):

    def test_writes_file_at_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / 'workspaces' / 'PROJ-1'
            ws.mkdir(parents=True)
            content = '# Plan\n1. Do X'
            ok = write_plan(ws, content)
            self.assertTrue(ok)
            target = ws / PLAN_FILENAME
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(), content)

    def test_creates_parent_directory_if_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / 'never-existed'
            ok = write_plan(ws, '# Plan')
            self.assertTrue(ok)
            self.assertTrue((ws / PLAN_FILENAME).is_file())

    def test_empty_plan_never_written(self) -> None:
        # A blank plan must not clobber a real one on a turn with no
        # ExitPlanMode call.
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / 'PROJ-1'
            ws.mkdir(parents=True)
            self.assertFalse(write_plan(ws, ''))
            self.assertFalse(write_plan(ws, '   \n  '))
            self.assertFalse((ws / PLAN_FILENAME).exists())

    def test_no_op_when_workspace_path_blank(self) -> None:
        self.assertFalse(write_plan('', '# Plan'))
        self.assertFalse(write_plan(None, '# Plan'))

    def test_atomic_no_partial_file_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            blocker = Path(td) / 'blocker'
            blocker.write_text('this is a file, not a directory')
            ok = write_plan(blocker, '# should fail')
            self.assertFalse(ok)
            self.assertEqual(
                blocker.read_text(), 'this is a file, not a directory',
            )


if __name__ == '__main__':
    unittest.main()
