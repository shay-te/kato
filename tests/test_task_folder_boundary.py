"""The task folder is the boundary — in the container AND in the prompt.

Two halves of one guarantee:

* the docker sandbox bind-mounts the TASK FOLDER (not one repo clone), so
  everything outside it does not exist to the agent, while every repo in the
  task stays reachable;
* the first chat turn carries the STRICT BOUNDARY block naming that same
  folder, so the agent is told the rule before it does anything.

They must agree. A prompt that names a wider scope than the mount produces
confident attempts at paths that cannot exist; a mount wider than the prompt
gives away reach nobody asked for.
"""

import os
import unittest

from sandbox_core_lib.sandbox_core_lib.manager import _container_workdir
from claude_core_lib.claude_core_lib.session.streaming import StreamingClaudeSession

TASK_FOLDER = os.path.normpath('/w/UNA-2981')
PRIMARY_REPO = os.path.join(TASK_FOLDER, 'backend')


def _mount(sandbox_root: str, cwd: str):
    session = StreamingClaudeSession.__new__(StreamingClaudeSession)
    session._sandbox_root = sandbox_root
    session._cwd = cwd
    return session._sandbox_mount()


class SandboxMountTests(unittest.TestCase):
    def test_task_folder_is_mounted_and_the_repo_stays_the_workdir(self) -> None:
        """Widening the mount must not relocate the agent."""
        root, subpath = _mount(TASK_FOLDER, PRIMARY_REPO)
        self.assertEqual(root, TASK_FOLDER)
        self.assertEqual(subpath, 'backend')

    def test_sibling_repos_are_inside_the_mount(self) -> None:
        """The whole point: a multi-repo task keeps cross-repo access."""
        root, _ = _mount(TASK_FOLDER, PRIMARY_REPO)
        sibling = os.path.join(TASK_FOLDER, 'client')
        self.assertTrue(os.path.commonpath([root, sibling]) == root)

    def test_without_a_task_folder_the_old_cwd_mount_is_kept(self) -> None:
        """No proven task folder ⇒ never widen the mount by guessing."""
        self.assertEqual(_mount('', PRIMARY_REPO), (PRIMARY_REPO, ''))

    def test_cwd_outside_the_task_folder_falls_back_to_cwd(self) -> None:
        """A mount root that doesn't contain the cwd would strand the agent."""
        self.assertEqual(
            _mount(TASK_FOLDER, '/elsewhere/repo'), ('/elsewhere/repo', ''),
        )

    def test_cwd_equal_to_the_task_folder_needs_no_subpath(self) -> None:
        self.assertEqual(_mount(TASK_FOLDER, TASK_FOLDER), (TASK_FOLDER, ''))


class ContainerWorkdirTests(unittest.TestCase):
    """The WORKDIR must never leave the bind mount."""

    def test_subpath_becomes_a_workspace_subdirectory(self) -> None:
        self.assertEqual(_container_workdir('backend'), '/workspace/backend')
        self.assertEqual(_container_workdir('a/b'), '/workspace/a/b')

    def test_climb_out_is_refused(self) -> None:
        self.assertEqual(_container_workdir('../../etc'), '/workspace')
        self.assertEqual(_container_workdir('..'), '/workspace')

    def test_absolute_input_is_refused_not_rewritten(self) -> None:
        """``/etc`` must not be quietly reinterpreted as ``/workspace/etc``."""
        self.assertEqual(_container_workdir('/etc'), '/workspace')

    def test_empty_and_blank_fall_back_to_the_mount_root(self) -> None:
        for value in ('', '   ', None):
            self.assertEqual(_container_workdir(value), '/workspace')


class FirstChatTurnBoundaryTests(unittest.TestCase):
    """A chat session used to be told the repo inventory but never the RULE."""

    def _prompt(self, workspace_root: str) -> str:
        from unittest.mock import Mock
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner,
            StreamingSessionDefaults,
        )
        manager = Mock()
        manager.get_record.return_value = None   # first spawn
        manager.get_session.return_value = None
        runner = PlanningSessionRunner(
            session_manager=manager, defaults=StreamingSessionDefaults(),
        )
        runner.resume_session_for_chat(
            task_id='UNA-2981',
            message='what changed here?',
            cwd=PRIMARY_REPO,
            workspace_root=workspace_root,
        )
        return manager.start_session.call_args.kwargs['initial_prompt']

    def test_first_turn_states_the_strict_boundary(self) -> None:
        prompt = self._prompt(TASK_FOLDER)
        self.assertIn('WORKSPACE SCOPE', prompt)
        self.assertIn(TASK_FOLDER, prompt)

    def test_the_boundary_is_the_very_first_thing_in_the_prompt(self) -> None:
        """"Read this first" has to actually be first."""
        prompt = self._prompt(TASK_FOLDER)
        self.assertTrue(prompt.startswith('WORKSPACE SCOPE'), prompt[:80])
        # ...and above the continuity/inventory preamble, not buried under it.
        self.assertLess(
            prompt.index('WORKSPACE SCOPE'), prompt.index('Continuity instruction'),
        )

    def test_boundary_names_the_task_folder_not_the_repo(self) -> None:
        """Naming the repo would contradict what the container mounts."""
        prompt = self._prompt(TASK_FOLDER)
        scope_section = prompt.split('Continuity instruction')[0]
        self.assertIn(TASK_FOLDER, scope_section)

    def test_the_operator_message_still_lands_last(self) -> None:
        self.assertTrue(self._prompt(TASK_FOLDER).endswith('what changed here?'))

    def test_no_task_folder_means_no_invented_boundary(self) -> None:
        prompt = self._prompt('')
        self.assertNotIn('WORKSPACE SCOPE', prompt)

    def test_sandbox_root_reaches_the_spawn(self) -> None:
        from unittest.mock import Mock
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner,
            StreamingSessionDefaults,
        )
        manager = Mock()
        manager.get_record.return_value = None
        runner = PlanningSessionRunner(
            session_manager=manager, defaults=StreamingSessionDefaults(),
        )
        runner.resume_session_for_chat(
            task_id='UNA-2981', message='hi', cwd=PRIMARY_REPO,
            workspace_root=TASK_FOLDER,
        )
        self.assertEqual(
            manager.start_session.call_args.kwargs['sandbox_root'], TASK_FOLDER,
        )


if __name__ == '__main__':
    unittest.main()
