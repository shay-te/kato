"""The agent writes files in the TASK folder, and the operator must see them.

Reported: the agent wrote ``embed_test.html`` into the task root so the
operator could open it in a browser, told them where it was, and clicking
it gave "path is outside the task workspace". The file existed; kato
refused to show a file it had just announced.

Cause: both the file reader and the file tree scoped themselves to the
REPO CLONES. The task folder that contains them was not in scope — even
though it is the task's own folder by definition.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'webserver'))

from kato_webserver.git_diff_utils import task_folder_file_tree


class TaskFolderTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        (self.root / 'embed_test.html').write_text('<html>', encoding='utf-8')
        (self.root / 'pr_description.md').write_text('desc', encoding='utf-8')
        (self.root / 'resume_prompt.md').write_text('resume', encoding='utf-8')
        (self.root / '.kato-meta').write_text('{}', encoding='utf-8')
        (self.root / '.kato-preflight').write_text('x', encoding='utf-8')
        self.clone = self.root / 'ob-love-ui'
        self.clone.mkdir()
        (self.clone / 'app.js').write_text('x', encoding='utf-8')

    def _names(self, repo_dirs=()):
        return [node['name'] for node in task_folder_file_tree(str(self.root), repo_dirs)]

    def test_the_agents_scratch_file_is_listed(self) -> None:
        self.assertIn('embed_test.html', self._names([str(self.clone)]))

    def test_katos_own_plumbing_is_hidden(self) -> None:
        # One predictable rule: dot-prefixed is plumbing.
        names = self._names([str(self.clone)])
        self.assertNotIn('.kato-meta', names)
        self.assertNotIn('.kato-preflight', names)

    def test_readable_deliverables_stay_visible(self) -> None:
        # pr_description.md and resume_prompt.md are things the operator
        # may well want to read — hiding everything kato writes would go
        # too far the other way.
        names = self._names([str(self.clone)])
        self.assertIn('pr_description.md', names)
        self.assertIn('resume_prompt.md', names)

    def test_repo_clones_are_NOT_duplicated(self) -> None:
        # Each clone already renders as its own tree; listing it here too
        # would show every file in the task twice.
        self.assertNotIn('ob-love-ui', self._names([str(self.clone)]))

    def test_the_clone_is_excluded_even_via_a_symlinked_temp_path(self) -> None:
        # macOS hands back /var for a path that resolves to /private/var.
        # Comparing raw strings matched nothing, so every clone was listed
        # twice — the exact bug this guards.
        unresolved = os.path.join(str(self.root), 'ob-love-ui')
        self.assertNotIn('ob-love-ui', self._names([unresolved]))
        resolved = str(self.clone.resolve())
        self.assertNotIn('ob-love-ui', self._names([resolved]))

    def test_a_non_repo_subfolder_is_listed_with_its_children(self) -> None:
        scratch = self.root / 'scratch'
        scratch.mkdir()
        (scratch / 'note.txt').write_text('x', encoding='utf-8')
        nodes = task_folder_file_tree(str(self.root), [str(self.clone)])
        folder = next(n for n in nodes if n['name'] == 'scratch')
        self.assertEqual([c['name'] for c in folder['children']], ['note.txt'])

    def test_an_empty_folder_is_not_rendered(self) -> None:
        (self.root / 'empty').mkdir()
        self.assertNotIn('empty', self._names([str(self.clone)]))

    def test_every_node_carries_an_absolute_path_the_reader_can_open(self) -> None:
        for node in task_folder_file_tree(str(self.root), [str(self.clone)]):
            with self.subTest(node=node['name']):
                self.assertTrue(os.path.isabs(node['path']))
                self.assertTrue(os.path.exists(node['path']))

    def test_a_git_repo_in_the_task_folder_is_not_unfolded(self) -> None:
        # A clone the caller did not list, or a bare mirror sitting beside
        # them, would otherwise unfold its whole object store into the
        # Files tab — thousands of SHA-named directories, and a payload
        # that changes with every commit.
        bare = self.root / 'origin.git'
        (bare / 'objects').mkdir(parents=True)
        (bare / 'HEAD').write_text('ref: refs/heads/main\n', encoding='utf-8')
        self.assertNotIn('origin.git', self._names([str(self.clone)]))

    def test_an_unlisted_working_clone_is_also_skipped(self) -> None:
        stray = self.root / 'other-repo'
        (stray / '.git').mkdir(parents=True)
        (stray / 'file.txt').write_text('x', encoding='utf-8')
        self.assertNotIn('other-repo', self._names([str(self.clone)]))

    def test_a_missing_task_folder_is_empty_not_an_error(self) -> None:
        self.assertEqual(task_folder_file_tree('/nope/not/here'), [])

    def test_a_blank_path_is_empty_not_an_error(self) -> None:
        self.assertEqual(task_folder_file_tree(''), [])


class TaskFolderTreeStaysFastTests(unittest.TestCase):
    """Listing a task folder takes a bounded time, whatever it accumulates.

    Reported: "takes forever!" — the Files pane sat on "Loading repos…". The
    git work for six repos took two seconds; walking the task folder took 110,
    because one task's helper_scripts held 470,819 files: a node_modules of
    222,245 and a test-run output folder of 247,069. Nothing the operator reads.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        (self.root / 'pr_description.md').write_text('desc', encoding='utf-8')
        self.helper = self.root / 'helper_scripts'
        self.helper.mkdir()
        (self.helper / 'run_tests.sh').write_text('x', encoding='utf-8')

    def _write(self, relative: str) -> None:
        path = self.helper / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('x', encoding='utf-8')

    def _paths(self, **kwargs):
        found = []

        def walk(nodes, prefix=''):
            for node in nodes:
                relative = f"{prefix}{node['name']}"
                if 'children' in node:
                    walk(node['children'], relative + '/')
                else:
                    found.append(relative)

        walk(task_folder_file_tree(str(self.root), (), **kwargs))
        return found

    def test_installed_and_generated_directories_are_not_walked(self) -> None:
        for relative in (
            'node_modules/pkg/index.js',
            'testbed/node_modules/dep/index.js',
            'venv/pyvenv.cfg', 'venv/lib/site.py',
            'pgdata/PG_VERSION', 'pgdata/base/1',
            '__pycache__/helper.cpython-311.pyc',
        ):
            self._write(relative)
        self.assertEqual(
            self._paths(), ['helper_scripts/run_tests.sh', 'pr_description.md'],
        )

    def test_a_folder_is_tooling_by_its_marker_not_its_name(self) -> None:
        # A ``venv`` with no pyvenv.cfg is the operator's own folder.
        self._write('venv/notes.md')
        self.assertIn('helper_scripts/venv/notes.md', self._paths())

    def test_the_walk_is_bounded_and_shallow_files_come_first(self) -> None:
        for run in range(60):
            for trace in range(10):
                self._write(f'testbed/runs/run-{run:03d}/trace-{trace}.json')
        paths = self._paths(max_entries=100)
        self.assertLessEqual(len(paths), 100)
        # Breadth-first: what an agent hands over sits near the top, so it is
        # listed before the budget runs out on the deep output folders.
        self.assertIn('pr_description.md', paths)
        self.assertIn('helper_scripts/run_tests.sh', paths)

    def test_shallow_files_are_listed_whichever_side_of_a_deep_folder_they_sort(self) -> None:
        # A depth-first walk spends the whole budget inside whichever big
        # folder it meets first, losing the notes that sort before or after it.
        for run in range(60):
            for trace in range(10):
                self._write(f'm-output/runs/run-{run:03d}/trace-{trace}.json')
        self._write('a-notes/summary.md')
        self._write('z-notes/summary.md')
        paths = self._paths(max_entries=100)
        self.assertIn('helper_scripts/a-notes/summary.md', paths)
        self.assertIn('helper_scripts/z-notes/summary.md', paths)

    def test_the_default_budget_lists_everything_in_an_ordinary_folder(self) -> None:
        for index in range(30):
            self._write(f'scripts/step-{index:02d}.py')
        self.assertEqual(len(self._paths()), 32)

    def test_a_zero_budget_lists_nothing(self) -> None:
        # A file that sorts first, so examining even ONE entry would show.
        (self.root / '0-first.md').write_text('x', encoding='utf-8')
        self.assertEqual(task_folder_file_tree(str(self.root), (), max_entries=0), [])


if __name__ == '__main__':
    unittest.main()
