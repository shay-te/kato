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


if __name__ == '__main__':
    unittest.main()
