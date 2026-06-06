import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_core_lib.helpers.workspace_repo_utils import sibling_repository_dirs


def _wm(repository_ids, paths):
    wm = MagicMock()
    wm.get.return_value = SimpleNamespace(repository_ids=repository_ids)
    wm.repository_path.side_effect = lambda _task, repo: paths[repo]
    return wm


class SiblingRepositoryDirsTests(unittest.TestCase):
    def test_returns_every_repo_except_the_cwd_one(self) -> None:
        wm = _wm(
            ['backend', 'client', 'core-lib'],
            {
                'backend': '/wk/UNA-1/backend',
                'client': '/wk/UNA-1/client',
                'core-lib': '/wk/UNA-1/core-lib',
            },
        )
        extras = sibling_repository_dirs(wm, 'UNA-1', '/wk/UNA-1/backend')
        self.assertEqual(extras, ['/wk/UNA-1/client', '/wk/UNA-1/core-lib'])

    def test_trailing_slash_on_cwd_still_excludes_it(self) -> None:
        wm = _wm(
            ['backend', 'client'],
            {'backend': '/wk/UNA-1/backend', 'client': '/wk/UNA-1/client'},
        )
        extras = sibling_repository_dirs(wm, 'UNA-1', '/wk/UNA-1/backend/')
        self.assertEqual(extras, ['/wk/UNA-1/client'])

    def test_single_repo_task_yields_no_extras(self) -> None:
        wm = _wm(['backend'], {'backend': '/wk/UNA-1/backend'})
        self.assertEqual(
            sibling_repository_dirs(wm, 'UNA-1', '/wk/UNA-1/backend'), [],
        )

    def test_none_manager_or_blank_task_is_empty(self) -> None:
        self.assertEqual(sibling_repository_dirs(None, 'UNA-1', '/x'), [])
        self.assertEqual(sibling_repository_dirs(MagicMock(), '', '/x'), [])

    def test_missing_workspace_is_empty(self) -> None:
        wm = MagicMock()
        wm.get.return_value = None
        self.assertEqual(sibling_repository_dirs(wm, 'UNA-1', '/x'), [])

    def test_per_repo_path_failure_is_skipped(self) -> None:
        wm = MagicMock()
        wm.get.return_value = SimpleNamespace(repository_ids=['a', 'b'])

        def path(_task, repo):
            if repo == 'a':
                raise RuntimeError('no path')
            return '/wk/UNA-1/b'

        wm.repository_path.side_effect = path
        self.assertEqual(
            sibling_repository_dirs(wm, 'UNA-1', '/wk/UNA-1/cwd'), ['/wk/UNA-1/b'],
        )

    def test_duplicate_paths_are_deduped(self) -> None:
        wm = _wm(
            ['a', 'b'],
            {'a': '/wk/UNA-1/shared', 'b': '/wk/UNA-1/shared'},
        )
        self.assertEqual(
            sibling_repository_dirs(wm, 'UNA-1', '/wk/UNA-1/cwd'),
            ['/wk/UNA-1/shared'],
        )


if __name__ == '__main__':
    unittest.main()
