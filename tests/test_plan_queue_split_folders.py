"""Finding and reading a split the operator already wrote as files.

Real folders on disk: what counts as a split folder, the task order of its
files, and every way a folder or a file is refused.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plan_queue_core_lib.plan_queue_core_lib.data.plan import SplitFile

from kato_core_lib.data_layers.service.plan_queue import split_folders
from kato_core_lib.data_layers.service.plan_queue.split_folders import (
    SplitFolder,
    SplitFolderError,
    find_split_folders,
    read_split_folder,
)


class SplitFolderTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='split-folders-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def _folder(self, name: str, files: dict[str, str]) -> Path:
        path = self.root / name
        path.mkdir()
        for file_name, text in files.items():
            (path / file_name).write_text(text, encoding='utf-8')
        return path

    def test_numbered_files_in_number_order_and_the_overview(self) -> None:
        self._folder('tasks', {'10-c.md': 'C', '2-b.md': 'B', '01-a.md': 'A', 'Readme.md': 'R', 'x.md': 'X'})
        self.assertEqual(find_split_folders(self.root), [
            SplitFolder(path='tasks', files=('01-a.md', '2-b.md', '10-c.md'), overview='Readme.md'),
        ])
        files, overview = read_split_folder(self.root, 'tasks/')
        self.assertEqual(files, [SplitFile('tasks/01-a.md', 'A'), SplitFile('tasks/2-b.md', 'B'),
                                 SplitFile('tasks/10-c.md', 'C')])
        self.assertEqual(overview, 'R')

    def test_no_overview(self) -> None:
        self._folder('tasks', {'01-a.md': 'A', '02-b.md': 'B'})
        self.assertEqual(find_split_folders(self.root)[0].to_dict(), {'path': 'tasks', 'files': 2, 'context': ''})
        self.assertEqual(read_split_folder(self.root, 'tasks')[1], '')

    def test_what_is_not_a_split_folder(self) -> None:
        self._folder('one', {'01-a.md': 'A', 'README.md': 'R'})            # one numbered file
        self._folder('memory', {'MEMORY.md': 'M', 'notes.md': 'N'})        # unnumbered
        self._folder('clone', {'01-a.md': 'A', '02-b.md': 'B'}).joinpath('.git').mkdir()
        self._folder('.hidden', {'01-a.md': 'A', '02-b.md': 'B'})
        nested = self._folder('nested', {'01-a.md': 'A'})
        (nested / '02-b.md').mkdir()                                         # a folder, not a file
        (self.root / 'plan.md').write_text('P', encoding='utf-8')
        real = self._folder('real', {'01-a.md': 'A', '02-b.md': 'B'})
        os.symlink(real, self.root / 'link')                                 # a link is never followed
        os.symlink(real / '01-a.md', nested / '03-c.md')
        self.assertEqual([folder.path for folder in find_split_folders(self.root)], ['real'])
        self.assertEqual(find_split_folders(self.root / 'missing'), [])

    def test_only_a_plain_folder_of_the_task_is_read(self) -> None:
        self._folder('tasks', {'01-a.md': 'A', '02-b.md': 'B'})
        for folder in ('', '  ', '../tasks', 'tasks/../..', 'a\\b', '.', '..', '.git'):
            with self.subTest(folder), self.assertRaises(SplitFolderError) as caught:
                read_split_folder(self.root, folder)
            self.assertIn('is not a folder of the task', str(caught.exception))
        with self.assertRaises(SplitFolderError) as caught:
            read_split_folder(self.root, 'nope')
        self.assertIn('nope/ holds no split', str(caught.exception))

    def test_unreadable_files_are_refused_with_the_reason(self) -> None:
        folder = self._folder('tasks', {'01-a.md': 'A'})
        (folder / '02-b.md').write_bytes(b'\xff\xfe\x00bad')
        with self.assertRaises(SplitFolderError) as caught:
            read_split_folder(self.root, 'tasks')
        self.assertIn('02-b.md is not readable text', str(caught.exception))
        (folder / '02-b.md').write_text('B' * 10, encoding='utf-8')
        with mock.patch.object(split_folders, 'MAX_FILE_BYTES', 5), self.assertRaises(SplitFolderError) as caught:
            read_split_folder(self.root, 'tasks')
        self.assertIn('02-b.md is too large to be a task', str(caught.exception))
        with mock.patch.object(Path, 'read_text', side_effect=PermissionError(13, 'Permission denied')), \
                self.assertRaises(SplitFolderError) as caught:
            read_split_folder(self.root, 'tasks')
        self.assertIn('could not be read: Permission denied', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
