"""Which task folder a path belongs to — and where that task's memory lives.

The agent kept writing memory outside the task folder, into its CLI's
per-user directory, because the prompt and the CLI named different places.
Both now resolve through these two helpers, so the rules below are the whole
contract:

* a session can start in the task folder OR in a repository clone under it;
  both must name the same task folder;
* a path outside the workspaces root has NO task folder — walking upward from
  an arbitrary checkout could name the operator's entire source tree;
* never a guess: unknown means ``''``.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    WORKSPACES_ROOT_ENV,
    task_folder_for,
    task_memory_directory,
)

ROOT = os.path.join(os.sep, 'work', 'spaces')
TASK = os.path.join(ROOT, 'PROJ-1')
REPO = os.path.join(TASK, 'client-app')


class TaskFolderForTests(unittest.TestCase):
    def test_a_repository_clone_resolves_to_its_task_folder(self) -> None:
        self.assertEqual(task_folder_for(REPO, ROOT), TASK)

    def test_the_task_folder_resolves_to_itself(self) -> None:
        # The case "parent of the working directory" gets wrong: it would name
        # the shared workspaces root, and every task would share one memory.
        self.assertEqual(task_folder_for(TASK, ROOT), TASK)

    def test_a_deep_file_path_still_names_the_task_folder(self) -> None:
        deep = os.path.join(REPO, 'src', 'module', 'file.py')
        self.assertEqual(task_folder_for(deep, ROOT), TASK)

    def test_the_workspaces_root_itself_has_no_task_folder(self) -> None:
        self.assertEqual(task_folder_for(ROOT, ROOT), '')

    def test_a_path_outside_the_root_has_no_task_folder(self) -> None:
        # An operator's own checkout elsewhere on the machine. Walking up from
        # it could name their whole source tree.
        elsewhere = os.path.join(os.sep, 'home', 'dev', 'src', 'some-repo')
        self.assertEqual(task_folder_for(elsewhere, ROOT), '')

    def test_a_prefix_sibling_of_the_root_is_not_inside_it(self) -> None:
        sibling = os.path.join(os.sep, 'work', 'spaces-old', 'PROJ-1', 'repo')
        self.assertEqual(task_folder_for(sibling, ROOT), '')

    def test_a_dotdot_escape_is_not_inside(self) -> None:
        escape = os.path.join(TASK, os.pardir, os.pardir, 'etc')
        self.assertEqual(task_folder_for(escape, ROOT), '')

    def test_a_relative_path_has_no_task_folder(self) -> None:
        self.assertEqual(task_folder_for(os.path.join('PROJ-1', 'repo'), ROOT), '')

    def test_trailing_separators_do_not_change_the_answer(self) -> None:
        self.assertEqual(task_folder_for(REPO + os.sep, ROOT + os.sep), TASK)

    def test_an_empty_path_has_no_task_folder(self) -> None:
        self.assertEqual(task_folder_for('', ROOT), '')
        self.assertEqual(task_folder_for(None, ROOT), '')

    def test_the_environment_supplies_the_root_when_none_is_given(self) -> None:
        with patch.dict(os.environ, {WORKSPACES_ROOT_ENV: ROOT}):
            self.assertEqual(task_folder_for(REPO), TASK)

    def test_an_unknown_root_means_unknown_not_a_guess(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(WORKSPACES_ROOT_ENV, None)
            self.assertEqual(task_folder_for(REPO), '')

    def test_an_explicit_empty_root_does_not_fall_back_to_the_environment(self) -> None:
        with patch.dict(os.environ, {WORKSPACES_ROOT_ENV: ROOT}):
            self.assertEqual(task_folder_for(REPO, ''), '')


class TaskMemoryDirectoryTests(unittest.TestCase):
    def test_memory_lives_directly_under_the_task_folder(self) -> None:
        self.assertEqual(task_memory_directory(TASK), os.path.join(TASK, 'memory'))

    def test_no_task_folder_means_no_memory_directory(self) -> None:
        self.assertEqual(task_memory_directory(''), '')
        self.assertEqual(task_memory_directory(None), '')

    def test_a_repository_clone_never_gets_memory_inside_it(self) -> None:
        # Composed the way every caller composes it.
        memory = task_memory_directory(task_folder_for(REPO, ROOT))
        self.assertEqual(memory, os.path.join(TASK, 'memory'))
        self.assertFalse(memory.startswith(REPO + os.sep))


if __name__ == '__main__':
    unittest.main()
