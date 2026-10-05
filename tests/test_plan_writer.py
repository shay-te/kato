"""Tests for the Kato-specific plan.md atomic writer.

The generic plan extractor is tested in
``agent_core_lib/agent_core_lib/tests/test_plan_capture_utils.py``; this
file covers only the workspace-on-disk write path that stays in
``kato_core_lib``.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from kato_core_lib.helpers import plan_writer
from kato_core_lib.helpers.plan_writer import (
    PLAN_CAPTURED_MARKER_FILENAME,
    PLAN_FILENAME,
    PLAN_PROGRESS_GUIDANCE,
    plan_captured_mtime,
    write_plan,
)


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


class PlanCaptureMarkerTests(unittest.TestCase):
    """``plan.md`` has two writers — kato capturing a NEW plan, and the agent
    ticking its progress checklist. Only the first may move the marker the UI
    auto-opens on, or every finished milestone would pop the plan open."""

    def test_a_capture_records_the_plan_files_own_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            self.assertTrue(write_plan(ws, '# Plan\n## Progress\n- [ ] a'))
            self.assertEqual(
                plan_captured_mtime(ws), (ws / PLAN_FILENAME).stat().st_mtime_ns,
            )

    def test_the_agent_ticking_a_box_does_not_move_the_marker(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            write_plan(ws, '# Plan\n## Progress\n- [ ] a\n- [ ] b')
            captured = plan_captured_mtime(ws)
            plan = ws / PLAN_FILENAME
            # The agent's Edit: same file, one box ticked, a later mtime.
            plan.write_text('# Plan\n## Progress\n- [x] a\n- [ ] b')
            os.utime(plan, ns=(captured + 10**9, captured + 10**9))
            self.assertEqual(plan_captured_mtime(ws), captured)
            self.assertGreater(plan.stat().st_mtime_ns, captured)

    def test_a_new_capture_moves_the_marker_forward(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            write_plan(ws, '# Plan one')
            first = plan_captured_mtime(ws)
            plan = ws / PLAN_FILENAME
            os.utime(plan, ns=(first - 10**9, first - 10**9))
            write_plan(ws, '# Plan two')
            self.assertEqual(plan_captured_mtime(ws), plan.stat().st_mtime_ns)
            self.assertNotEqual(plan_captured_mtime(ws), 0)

    def test_no_marker_reads_as_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(plan_captured_mtime(td), 0)
        self.assertEqual(plan_captured_mtime(''), 0)
        self.assertEqual(plan_captured_mtime(None), 0)

    def test_a_garbled_marker_reads_as_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / PLAN_CAPTURED_MARKER_FILENAME).write_text('not a number')
            self.assertEqual(plan_captured_mtime(td), 0)

    def test_a_marker_that_cannot_be_written_is_removed_not_left_stale(self) -> None:
        # A stale marker would hide the new plan from the auto-open; no marker
        # falls back to the file's mtime, which still shows it.
        real_write = plan_writer.atomic_write_text

        def plan_ok_marker_fails(path, content, **kwargs):
            if path.name == PLAN_CAPTURED_MARKER_FILENAME:
                return False
            return real_write(path, content, **kwargs)

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            write_plan(ws, '# Plan one')
            with patch.object(plan_writer, 'atomic_write_text', plan_ok_marker_fails):
                self.assertTrue(write_plan(ws, '# Plan two'))
            self.assertFalse((ws / PLAN_CAPTURED_MARKER_FILENAME).exists())
            self.assertEqual(plan_captured_mtime(ws), 0)

    def test_a_failed_plan_write_leaves_the_marker_alone(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            write_plan(ws, '# Plan one')
            captured = plan_captured_mtime(ws)
            with patch.object(plan_writer, 'atomic_write_text', return_value=False):
                self.assertFalse(write_plan(ws, '# Plan two'))
            self.assertEqual(plan_captured_mtime(ws), captured)


class AdoptUncapturedPlanTests(unittest.TestCase):

    def test_an_agent_written_plan_gets_its_marker(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            plan = ws / PLAN_FILENAME
            plan.write_text('# Plan\n## Progress\n- [ ] a')
            self.assertTrue(plan_writer.adopt_uncaptured_plan(ws))
            self.assertEqual(plan_captured_mtime(ws), plan.stat().st_mtime_ns)

    def test_an_existing_marker_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            write_plan(ws, '# Plan')
            captured = plan_captured_mtime(ws)
            plan = ws / PLAN_FILENAME
            os.utime(plan, ns=(captured + 10**9, captured + 10**9))
            self.assertFalse(plan_writer.adopt_uncaptured_plan(ws))
            self.assertEqual(plan_captured_mtime(ws), captured)

    def test_nothing_to_adopt_without_a_plan(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(plan_writer.adopt_uncaptured_plan(td))
            self.assertFalse((Path(td) / PLAN_CAPTURED_MARKER_FILENAME).exists())
        self.assertFalse(plan_writer.adopt_uncaptured_plan(''))


class PlanProgressGuidanceTests(unittest.TestCase):
    """The rule every spawn's system prompt carries."""

    def test_every_plan_ends_with_a_checkbox_section(self) -> None:
        self.assertIn('## Progress', PLAN_PROGRESS_GUIDANCE)
        self.assertIn('- [ ]', PLAN_PROGRESS_GUIDANCE)
        self.assertIn('nothing comes after it', PLAN_PROGRESS_GUIDANCE)

    def test_it_names_the_file_kato_writes_and_says_to_tick_it(self) -> None:
        self.assertIn(f'`{PLAN_FILENAME}`', PLAN_PROGRESS_GUIDANCE)
        self.assertIn('task folder', PLAN_PROGRESS_GUIDANCE)
        self.assertIn('`- [x]`', PLAN_PROGRESS_GUIDANCE)

    def test_it_steers_away_from_the_clis_own_plans_file(self) -> None:
        self.assertIn('~/.claude/plans/', PLAN_PROGRESS_GUIDANCE)

    def test_it_forbids_the_agent_overwriting_the_captured_plan(self) -> None:
        # Live run on the real CLI: told nothing about who saves the plan, the
        # agent ``cat >``-ed a stub over kato's captured plan.md, wiping its
        # context and steps and keeping only the checklist.
        self.assertIn('You never save the plan yourself', PLAN_PROGRESS_GUIDANCE)
        self.assertIn('Never rewrite or overwrite', PLAN_PROGRESS_GUIDANCE)

    def test_a_plan_that_never_reached_kato_is_created_once(self) -> None:
        # Live run on the real CLI: ExitPlanMode came through empty, nothing
        # was captured, and an agent told "do not create it" tracked nothing.
        self.assertIn('If it is missing', PLAN_PROGRESS_GUIDANCE)
        self.assertIn('create it once', PLAN_PROGRESS_GUIDANCE)

    def test_it_ticks_one_milestone_at_a_time_with_a_targeted_edit(self) -> None:
        # Same run: one ``sed`` ticked every box at the end of the work.
        self.assertIn('before you start the next', PLAN_PROGRESS_GUIDANCE)
        self.assertIn('never a blanket replace', PLAN_PROGRESS_GUIDANCE)


if __name__ == '__main__':
    unittest.main()
