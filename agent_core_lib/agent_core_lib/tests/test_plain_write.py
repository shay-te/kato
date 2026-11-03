"""Which writes "Edit automatically" may let through with no click.

Real folders and real symlinks: the whole point of this check over the lexical
one is what is actually on disk.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_core_lib.agent_core_lib.helpers.plain_write import WRITE_TOOLS, is_plain_write


class _TaskFolderCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # Resolved: the temp folder is itself behind a symlink on macOS.
        self.base = Path(os.path.realpath(tmp.name))
        self.task = self.base / 'workspaces' / 'T-1'
        self.repo = self.task / 'api'
        self.sibling = self.task / 'web'
        self.repo.mkdir(parents=True)
        self.sibling.mkdir()

    def plain(self, path, tool='Write', **kwargs) -> bool:
        return is_plain_write(tool, {'file_path': str(path)}, str(self.repo), **kwargs)


class InsideTheFolderTests(_TaskFolderCase):
    def test_a_file_in_the_repository(self) -> None:
        self.assertTrue(self.plain(self.repo / 'src' / 'a.py'))

    def test_a_relative_path_starts_at_the_working_folder(self) -> None:
        self.assertTrue(self.plain('src/a.py'))

    def test_another_repository_of_the_same_task(self) -> None:
        self.assertTrue(self.plain(self.sibling / 'a.py'))

    def test_a_file_in_the_task_folder_itself(self) -> None:
        self.assertTrue(self.plain(self.task / 'memory' / 'notes.md'))

    def test_every_writing_tool(self) -> None:
        for tool in WRITE_TOOLS:
            with self.subTest(tool=tool):
                self.assertTrue(self.plain('a.py', tool=tool))

    def test_a_notebook_names_its_path_under_another_key(self) -> None:
        self.assertTrue(is_plain_write(
            'NotebookEdit', {'notebook_path': 'a.ipynb'}, str(self.repo),
        ))

    def test_names_that_only_look_like_control_files(self) -> None:
        for name in ('.gitignore', '.gitattributes', '.github/workflows/ci.yml',
                     'git/config', 'src/.gitkeep', 'claude/notes.md'):
            with self.subTest(name=name):
                self.assertTrue(self.plain(name))


class OutsideTheFolderTests(_TaskFolderCase):
    def test_somewhere_else(self) -> None:
        self.assertFalse(self.plain(self.base / 'elsewhere.py'))

    def test_another_task(self) -> None:
        other = self.task.parent / 'T-2' / 'api' / 'a.py'
        self.assertFalse(self.plain(other))

    def test_climbing_out(self) -> None:
        self.assertFalse(self.plain('../../T-2/api/a.py'))

    def test_a_folder_that_starts_with_the_same_name(self) -> None:
        self.assertFalse(self.plain(self.task.parent / 'T-10' / 'a.py'))

    def test_one_path_of_two_outside_is_enough(self) -> None:
        self.assertFalse(is_plain_write(
            'Write', {'file_path': 'a.py', 'path': str(self.base / 'b.py')}, str(self.repo),
        ))


class SymlinkTests(_TaskFolderCase):
    """Inside by name and outside on disk: a repository can carry these."""

    def setUp(self) -> None:
        super().setUp()
        self.outside = self.base / 'secrets'
        self.outside.mkdir()

    def test_a_folder_link_out_of_the_task(self) -> None:
        (self.repo / 'link').symlink_to(self.outside)
        self.assertFalse(self.plain(self.repo / 'link' / 'authorized_keys'))

    def test_a_file_link_out_of_the_task(self) -> None:
        target = self.outside / 'config'
        target.write_text('x', encoding='utf-8')
        (self.repo / 'link.py').symlink_to(target)
        self.assertFalse(self.plain(self.repo / 'link.py'))

    def test_a_link_to_a_control_file_in_the_task(self) -> None:
        (self.repo / '.git').mkdir()
        (self.repo / 'link').symlink_to(self.repo / '.git')
        self.assertFalse(self.plain(self.repo / 'link' / 'config'))

    def test_a_link_that_stays_inside_is_fine(self) -> None:
        (self.repo / 'src').mkdir()
        (self.repo / 'link').symlink_to(self.repo / 'src')
        self.assertTrue(self.plain(self.repo / 'link' / 'a.py'))

    def test_a_task_folder_reached_through_a_link(self) -> None:
        alias = self.base / 'alias'
        alias.symlink_to(self.task.parent)
        self.assertTrue(is_plain_write(
            'Write', {'file_path': str(alias / 'T-1' / 'api' / 'a.py')},
            str(alias / 'T-1' / 'api'),
        ))


class ControlFileTests(_TaskFolderCase):
    def test_what_decides_what_runs_next(self) -> None:
        for name in ('.git/config', '.git/hooks/pre-commit', '.claude/settings.json',
                     '.claude/settings.local.json', '.mcp.json', '.claude.json',
                     '.gitmodules', '.gitconfig', '.vscode/tasks.json', '.idea/workspace.xml'):
            with self.subTest(name=name):
                self.assertFalse(self.plain(name))

    def test_at_any_depth(self) -> None:
        self.assertFalse(self.plain('packages/a/.claude/settings.json'))

    def test_in_the_task_folder_above_the_repository(self) -> None:
        self.assertFalse(self.plain(self.task / '.claude' / 'settings.json'))

    def test_the_spellings_a_disk_treats_as_the_same(self) -> None:
        for name in ('.GIT/config', '.git./config', '.git /config',
                     '.git::$INDEX_ALLOCATION/config', '.MCP.JSON'):
            with self.subTest(name=name):
                self.assertFalse(self.plain(name))

    def test_the_host_names_its_own_files(self) -> None:
        self.assertFalse(self.plain(self.task / '.host-meta.json', reserved_prefixes=('.host-',)))
        self.assertTrue(self.plain(self.task / '.hostess.json', reserved_prefixes=('.host-',)))


class AllowedFileTests(_TaskFolderCase):
    def test_a_file_the_host_hands_the_agent(self) -> None:
        lessons = self.base / 'lessons.md'
        self.assertTrue(self.plain(lessons, allowed_paths=(str(lessons),)))

    def test_only_that_file(self) -> None:
        lessons = self.base / 'lessons.md'
        self.assertFalse(self.plain(self.base / 'other.md', allowed_paths=(str(lessons),)))

    def test_a_blank_entry_allows_nothing(self) -> None:
        self.assertFalse(self.plain(self.base / 'other.md', allowed_paths=('',)))


class NothingToJudgeTests(_TaskFolderCase):
    def test_a_tool_that_is_not_a_write(self) -> None:
        for tool in ('Bash', 'Read', 'write', '', 'mcp__files__write'):
            with self.subTest(tool=tool):
                self.assertFalse(self.plain('a.py', tool=tool))

    def test_a_write_that_names_no_path(self) -> None:
        self.assertFalse(is_plain_write('Write', {'content': 'x'}, str(self.repo)))
        self.assertFalse(is_plain_write('Write', {'file_path': '  '}, str(self.repo)))
        self.assertFalse(is_plain_write('Write', 'not a dict', str(self.repo)))

    def test_no_working_folder(self) -> None:
        self.assertFalse(is_plain_write('Write', {'file_path': '/a/b.py'}, ''))

    def test_a_path_the_system_cannot_read(self) -> None:
        self.assertFalse(self.plain('a\x00b.py'))

    def test_a_nul_is_refused_even_where_realpath_lets_it_through(self) -> None:
        # Windows' realpath passes a NUL through where POSIX refuses it, and
        # CI on Windows approved 'a\x00b.py' until the check stopped leaning
        # on realpath to refuse it.
        lenient = lambda path, *args, **kwargs: os.path.normpath(path)  # noqa: E731
        with patch('agent_core_lib.agent_core_lib.helpers.plain_write.os.path.realpath', lenient):
            self.assertTrue(self.plain('a.py'))
            self.assertFalse(self.plain('a\x00b.py'))


if __name__ == '__main__':
    unittest.main()
