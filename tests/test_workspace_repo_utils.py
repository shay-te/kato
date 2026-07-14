import unittest
from unittest.mock import MagicMock

from kato_core_lib.helpers.workspace_repo_utils import sibling_repository_dirs


class SiblingRepositoryDirsTests(unittest.TestCase):
    def test_returns_the_whole_task_workspace_folder(self) -> None:
        wm = MagicMock()
        wm.get.return_value = object()
        wm.workspace_path.return_value = '/wk/UNA-1'
        self.assertEqual(sibling_repository_dirs(wm, 'UNA-1'), ['/wk/UNA-1'])

    def test_a_repo_attached_after_spawn_is_covered_by_the_folder_scope(self) -> None:
        # Regression: the old enumerated-repo-id approach couldn't see a
        # repo attached to the task mid-conversation until the session
        # respawned. The whole-folder scope covers it immediately since
        # the new clone lands inside the SAME returned folder.
        wm = MagicMock()
        wm.get.return_value = object()
        wm.workspace_path.return_value = '/wk/UNA-1'
        before = sibling_repository_dirs(wm, 'UNA-1')
        # Simulate a repo attached later — workspace_path is unaffected,
        # it's still the same task folder the new clone lands inside.
        after = sibling_repository_dirs(wm, 'UNA-1')
        self.assertEqual(before, after)
        self.assertEqual(after, ['/wk/UNA-1'])

    def test_none_manager_or_blank_task_is_empty(self) -> None:
        self.assertEqual(sibling_repository_dirs(None, 'UNA-1'), [])
        self.assertEqual(sibling_repository_dirs(MagicMock(), ''), [])

    def test_missing_workspace_is_empty(self) -> None:
        wm = MagicMock()
        wm.get.return_value = None
        self.assertEqual(sibling_repository_dirs(wm, 'UNA-1'), [])

    def test_get_failure_is_empty(self) -> None:
        wm = MagicMock()
        wm.get.side_effect = RuntimeError('boom')
        self.assertEqual(sibling_repository_dirs(wm, 'UNA-1'), [])

    def test_workspace_path_failure_is_empty(self) -> None:
        wm = MagicMock()
        wm.get.return_value = object()
        wm.workspace_path.side_effect = RuntimeError('boom')
        self.assertEqual(sibling_repository_dirs(wm, 'UNA-1'), [])

    def test_blank_workspace_path_is_empty(self) -> None:
        wm = MagicMock()
        wm.get.return_value = object()
        wm.workspace_path.return_value = ''
        self.assertEqual(sibling_repository_dirs(wm, 'UNA-1'), [])


if __name__ == '__main__':
    unittest.main()
