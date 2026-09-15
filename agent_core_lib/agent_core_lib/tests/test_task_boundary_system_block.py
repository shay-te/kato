"""The persistent task-folder rule that rides in the agent's SYSTEM prompt.

The operator had to explain the boundary by hand, again and again: "i am tired
of explaining every time. make sure i don't need to explain again not to go
outside the task folder", "tell him that his memory is ONLY under the task
folder", and "don't use relative paths that will make it look like you are
trying to access outside of the task folder".

The full boundary block only ever rode in the first user message, which a
resumed session never receives and a long conversation summarises away. This
short block goes in the system prompt, which every launch carries.
"""
from __future__ import annotations

import os
import unittest

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    task_boundary_system_block,
)

TASK = os.path.join(os.sep, 'work', 'spaces', 'PROJ-3')
DOC = os.path.join(os.sep, 'work', 'spaces', 'architecture.md')
LESSONS = os.path.join(os.sep, 'work', 'spaces', 'lessons.md')


class TaskBoundarySystemBlockTests(unittest.TestCase):
    def test_it_names_the_task_folder(self) -> None:
        self.assertIn(f'YOUR TASK FOLDER IS: {TASK}', task_boundary_system_block(TASK))

    def test_it_says_never_to_leave_the_folder(self) -> None:
        block = task_boundary_system_block(TASK).lower()
        self.assertIn('only inside that folder', block)
        self.assertIn('not once', block)

    def test_memory_is_only_under_the_task_folder(self) -> None:
        block = task_boundary_system_block(TASK)
        self.assertIn(f'YOUR MEMORY IS ONLY IN: {os.path.join(TASK, "memory")}{os.sep}', block)
        self.assertIn('nowhere else', block)

    def test_it_requires_absolute_paths_and_forbids_relative_hops(self) -> None:
        # A relative path or a `cd ..` hop reads as an attempt to leave, and
        # stops the work for an approval the operator then has to explain.
        block = task_boundary_system_block(TASK)
        self.assertIn(f'ABSOLUTE paths that start with {TASK}{os.sep}', block)
        self.assertIn('relative paths', block)
        self.assertIn('``..``', block)
        self.assertIn('``cd``', block)

    def test_it_offers_a_variable_anchored_on_the_task_folder(self) -> None:
        # The shell scope check substitutes ``T=<folder>`` into ``$T/…``, so
        # this form never raises an approval and keeps long commands readable.
        self.assertIn(f'``T={TASK}`` then ``$T/', task_boundary_system_block(TASK))

    def test_outside_files_are_listed_with_exact_path_access_only(self) -> None:
        block = task_boundary_system_block(TASK, outside_files=[DOC, LESSONS])
        self.assertIn(f'  - {DOC}', block)
        self.assertIn(f'  - {LESSONS}', block)
        # The popup that prompted this: the agent `cd`-ed into the doc's folder
        # to grep it. Exact-path tool access is the route that never asks.
        self.assertIn('exact absolute path with the Read, Edit or Grep tool', block)
        self.assertIn('Never ``cd`` into its folder', block)

    def test_a_file_already_inside_the_folder_is_not_an_exception(self) -> None:
        inside = os.path.join(TASK, 'notes.md')
        block = task_boundary_system_block(TASK, outside_files=[inside])
        self.assertNotIn('The ONLY files outside', block)
        self.assertNotIn(f'  - {inside}', block)

    def test_no_outside_files_means_no_exception_list(self) -> None:
        self.assertNotIn('The ONLY files outside', task_boundary_system_block(TASK))

    def test_duplicates_and_blanks_are_dropped(self) -> None:
        block = task_boundary_system_block(TASK, outside_files=[DOC, '', DOC, None])
        self.assertEqual(block.count(f'  - {DOC}'), 1)

    def test_it_ends_by_saying_to_ask_rather_than_look(self) -> None:
        block = task_boundary_system_block(TASK, outside_files=[DOC])
        self.assertTrue(block.rstrip().endswith('Do not go and look for it.'))

    def test_no_task_folder_means_no_block(self) -> None:
        # A boundary without a path is not a boundary.
        self.assertEqual(task_boundary_system_block(''), '')
        self.assertEqual(task_boundary_system_block(None, outside_files=[DOC]), '')

    def test_it_names_no_agent_vendor(self) -> None:
        # The host product's own name is covered by tests/test_corelib_agnostic_gate.
        block = task_boundary_system_block(TASK, outside_files=[DOC]).lower()
        for brand in ('claude', 'codex', 'openhands'):
            with self.subTest(brand=brand):
                self.assertNotIn(brand, block)


if __name__ == '__main__':
    unittest.main()
