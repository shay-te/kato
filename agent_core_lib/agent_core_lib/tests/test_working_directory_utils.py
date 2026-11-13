"""Where a CLI with no ``--add-dir`` starts, for a task with several repositories."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from agent_core_lib.agent_core_lib.helpers.working_directory_utils import single_project_root


class SingleProjectRootTests(unittest.TestCase):
    def test_one_repository_is_its_own_root(self) -> None:
        self.assertEqual(single_project_root('/w/task/a'), '/w/task/a')
        self.assertEqual(single_project_root('/w/task/a', ['', None]), '/w/task/a')
        self.assertEqual(single_project_root(''), '')

    def test_sibling_repositories_start_in_the_folder_that_holds_them(self) -> None:
        self.assertEqual(single_project_root('/w/task/a', ['/w/task/b', '/w/task/c']), '/w/task')

    def test_a_common_root_of_the_whole_disk_is_refused(self) -> None:
        self.assertEqual(single_project_root('/a/x', ['/b/y']), '/a/x')

    def test_paths_that_cannot_be_compared_keep_the_first(self) -> None:
        with patch.object(os.path, 'commonpath', side_effect=ValueError('mixed')):
            self.assertEqual(single_project_root('/a', ['b']), '/a')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
