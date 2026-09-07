import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from kato_core_lib.helpers.workspace_repo_utils import (
    resolve_session_cwd,
    sibling_repository_dirs,
    task_repository_clones,
    task_workspace_root,
)


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


class TaskWorkspaceRootTests(unittest.TestCase):
    """The task folder the boundary block names and the sandbox mounts.

    Shared by the chat-send route AND the wait-planning / wait-editing hold
    spawn. The hold spawn had no equivalent at all, which is why a hold
    session opened with no task-folder boundary and the operator had to
    paste the clone path into the chat.
    """

    def test_returns_the_folder_when_it_exists_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'UNA-1'
            root.mkdir()
            wm = MagicMock()
            wm.workspace_path.return_value = str(root)
            self.assertEqual(task_workspace_root(wm, 'UNA-1'), str(root))

    def test_a_folder_that_is_not_on_disk_yet_is_empty(self) -> None:
        # Better an absent boundary than one naming a path that does not
        # exist — the agent would go looking and find nothing.
        with tempfile.TemporaryDirectory() as tmp:
            wm = MagicMock()
            wm.workspace_path.return_value = str(Path(tmp) / 'never-created')
            self.assertEqual(task_workspace_root(wm, 'UNA-1'), '')

    def test_none_manager_blank_task_and_failures_are_empty(self) -> None:
        self.assertEqual(task_workspace_root(None, 'UNA-1'), '')
        self.assertEqual(task_workspace_root(MagicMock(), ''), '')
        failing = MagicMock()
        failing.workspace_path.side_effect = RuntimeError('boom')
        self.assertEqual(task_workspace_root(failing, 'UNA-1'), '')
        blank = MagicMock()
        blank.workspace_path.return_value = ''
        self.assertEqual(task_workspace_root(blank, 'UNA-1'), '')


class ResolveSessionCwdTests(unittest.TestCase):
    """A task WITH a workspace may only ever spawn inside it.

    A bad ``cwd`` used to be self-perpetuating: a session spawned without one
    took kato's own working directory, that value was persisted onto the
    session record, and every later respawn read it back and started there
    again — so an ob-love ticket ran the agent inside kato's own sources with
    the sandbox scoped to match. The spawn side now refuses an empty cwd;
    this is the read side, correcting records the old behaviour poisoned.

    Real directories on disk, not stubs — the whole check is "is this path
    inside that one", and a stub that answers yes proves nothing.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.workspace = self.root / 'workspaces' / 'UNA-1'
        self.client = self.workspace / 'client'
        self.server = self.workspace / 'server'
        for path in (self.client, self.server):
            path.mkdir(parents=True)
        # The operator's own checkout, well outside the workspace.
        self.foreign = self.root / 'dev' / 'kato'
        self.foreign.mkdir(parents=True)

    def _manager(self, repository_ids=('client', 'server')):
        wm = MagicMock()
        wm.workspace_path.return_value = str(self.workspace)
        wm.get.return_value = MagicMock(repository_ids=list(repository_ids))
        wm.repository_path.side_effect = lambda task_id, repo_id: self.workspace / repo_id
        return wm

    def test_a_cwd_inside_the_workspace_is_kept(self) -> None:
        self.assertEqual(
            resolve_session_cwd(self._manager(), 'UNA-1', str(self.client)),
            str(self.client),
        )

    def test_a_cwd_outside_the_workspace_is_replaced_with_the_task_clone(self) -> None:
        # The reported failure: the record said kato's own checkout.
        self.assertEqual(
            resolve_session_cwd(self._manager(), 'UNA-1', str(self.foreign)),
            str(self.client),
        )

    def test_an_empty_cwd_resolves_to_the_task_clone(self) -> None:
        self.assertEqual(
            resolve_session_cwd(self._manager(), 'UNA-1', ''),
            str(self.client),
        )

    def test_a_sibling_prefix_is_not_treated_as_inside(self) -> None:
        # ``…/UNA-1-old`` starts with ``…/UNA-1`` as a STRING but is a
        # different task's folder; a prefix check would hand it over.
        sibling = self.workspace.parent / 'UNA-1-old'
        sibling.mkdir()
        self.assertEqual(
            resolve_session_cwd(self._manager(), 'UNA-1', str(sibling)),
            str(self.client),
        )

    def test_falls_back_to_the_task_folder_only_with_no_clones(self) -> None:
        # Narrowest true answer when the task genuinely has no repo yet.
        self.assertEqual(
            resolve_session_cwd(self._manager(repository_ids=()), 'UNA-1', str(self.foreign)),
            str(self.workspace),
        )

    def test_a_task_without_a_workspace_keeps_its_candidate(self) -> None:
        # Adopted-cwd / legacy single-clone installs: there is nothing to
        # validate against, and inventing a boundary is worse than trusting
        # the operator's own directory.
        wm = MagicMock()
        wm.workspace_path.return_value = str(self.root / 'never-created')
        self.assertEqual(
            resolve_session_cwd(wm, 'UNA-1', str(self.foreign)), str(self.foreign),
        )
        self.assertEqual(resolve_session_cwd(None, 'UNA-1', str(self.foreign)), str(self.foreign))

    def test_repository_clones_lists_only_directories_on_disk(self) -> None:
        wm = self._manager(repository_ids=('client', 'server', 'never-cloned'))
        self.assertEqual(
            task_repository_clones(wm, 'UNA-1'),
            [str(self.client), str(self.server)],
        )

    def test_repository_clones_degrade_to_empty(self) -> None:
        self.assertEqual(task_repository_clones(None, 'UNA-1'), [])
        self.assertEqual(task_repository_clones(MagicMock(), ''), [])
        missing = MagicMock()
        missing.get.return_value = None
        self.assertEqual(task_repository_clones(missing, 'UNA-1'), [])
        failing = MagicMock()
        failing.get.side_effect = RuntimeError('boom')
        self.assertEqual(task_repository_clones(failing, 'UNA-1'), [])


if __name__ == '__main__':
    unittest.main()
