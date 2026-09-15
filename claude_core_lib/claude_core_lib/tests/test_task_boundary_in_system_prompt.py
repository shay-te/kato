"""Every launch's system prompt carries the task-folder boundary.

The strict boundary used to ride only in a session's first user message. A
resumed session never receives that message, and a long conversation is
summarised until it is gone — so the operator kept having to tell the agent,
by hand, not to leave the task folder and to keep its memory inside it. The
system prompt is re-sent on every launch, so the rule is built into it here.

Driven through the real builder with real files on disk; only the workspaces
root is set through the environment the host exports.
"""
from __future__ import annotations

import logging
import os
import tempfile
import unittest
from unittest.mock import patch

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    WORKSPACES_ROOT_ENV,
)
from claude_core_lib.claude_core_lib.helpers.spawn_utils import (
    build_appended_system_prompt,
)

_LOGGER = logging.getLogger('test-task-boundary')


class TaskBoundaryInSystemPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # Resolved: macOS hands out /var/... for a path that is /private/var/...
        self.root = os.path.realpath(self._tmp.name)
        self.task = os.path.join(self.root, 'PROJ-8')
        self.repo = os.path.join(self.task, 'billing-api')
        os.makedirs(self.repo)
        # Shared across tasks, so they live at the workspaces root — outside
        # every task folder, which is exactly why they need naming.
        self.doc = os.path.join(self.root, 'architecture.md')
        self.lessons = os.path.join(self.root, 'lessons.md')
        with open(self.doc, 'w', encoding='utf-8') as handle:
            handle.write('# Architecture\n')
        with open(self.lessons, 'w', encoding='utf-8') as handle:
            handle.write('- a lesson\n')

    def _build(self, cwd, *, root=None, doc=None, lessons=None):
        env = {WORKSPACES_ROOT_ENV: self.root if root is None else root}
        with patch.dict(os.environ, env):
            return build_appended_system_prompt(
                architecture_doc_path=self.doc if doc is None else doc,
                lessons_path=self.lessons if lessons is None else lessons,
                docker_mode_on=False,
                logger=_LOGGER,
                cwd=cwd,
            )

    def test_the_boundary_is_the_first_section(self) -> None:
        prompt = self._build(self.repo)
        self.assertTrue(prompt.startswith('# Task folder boundary'), prompt[:80])

    def test_it_names_this_task_folder_even_from_a_repo_clone(self) -> None:
        prompt = self._build(self.repo)
        self.assertIn(f'YOUR TASK FOLDER IS: {self.task}', prompt)
        self.assertNotIn(f'YOUR TASK FOLDER IS: {self.repo}', prompt)

    def test_memory_is_only_under_this_task_folder(self) -> None:
        prompt = self._build(self.repo)
        self.assertIn(
            f'YOUR MEMORY IS ONLY IN: {os.path.join(self.task, "memory")}{os.sep}', prompt,
        )

    def test_the_shared_files_are_named_as_exact_path_exceptions(self) -> None:
        # The popup that started this: the agent `cd`-ed into the folder holding
        # the architecture doc to grep it. Exact-path access is exempt; the
        # folder is not.
        prompt = self._build(self.repo)
        self.assertIn(f'  - {self.doc}', prompt)
        self.assertIn(f'  - {self.lessons}', prompt)
        self.assertIn('Never ``cd`` into its folder', prompt)

    def test_a_file_whose_directive_was_not_emitted_is_not_listed(self) -> None:
        missing = os.path.join(self.root, 'no-such-lessons.md')
        prompt = self._build(self.repo, lessons=missing)
        self.assertNotIn(missing, prompt)
        self.assertIn(f'  - {self.doc}', prompt)

    def test_the_boundary_is_still_there_on_a_resumed_style_launch(self) -> None:
        # The builder is the same for a resume: nothing depends on a first
        # message, which is the whole point.
        first = self._build(self.repo)
        again = self._build(self.repo)
        self.assertEqual(first, again)
        self.assertIn('# Task folder boundary', again)

    def test_no_known_task_folder_means_no_boundary(self) -> None:
        prompt = self._build(self.repo, root='')
        self.assertNotIn('# Task folder boundary', prompt)

    def test_a_checkout_outside_the_workspaces_root_gets_no_boundary(self) -> None:
        elsewhere = tempfile.mkdtemp()
        self.addCleanup(lambda: os.rmdir(elsewhere))
        prompt = self._build(elsewhere)
        self.assertNotIn('# Task folder boundary', prompt)

    def test_no_working_directory_gets_no_boundary(self) -> None:
        self.assertNotIn('# Task folder boundary', self._build(''))


class OneShotLaunchCarriesTheBoundaryTests(unittest.TestCase):
    """The launcher, not just the builder: a real one-shot command started in a
    repository clone must hand the boundary to the CLI in
    ``--append-system-prompt``."""

    def test_the_launch_command_carries_the_boundary(self) -> None:
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = os.path.realpath(tmp.name)
        task = os.path.join(root, 'PROJ-9')
        repo = os.path.join(task, 'web-app')
        os.makedirs(repo)
        doc = os.path.join(root, 'architecture.md')
        with open(doc, 'w', encoding='utf-8') as handle:
            handle.write('# Architecture\n')

        client = ClaudeCliClient(binary='claude', architecture_doc_path=doc)
        with patch.dict(os.environ, {WORKSPACES_ROOT_ENV: root}):
            command = client._build_command(
                additional_dirs=[], agent_session_id='', cwd=repo,
            )
        self.assertIn('--append-system-prompt', command)
        prompt = command[command.index('--append-system-prompt') + 1]
        self.assertIn(f'YOUR TASK FOLDER IS: {task}', prompt)
        self.assertIn(f'YOUR MEMORY IS ONLY IN: {os.path.join(task, "memory")}{os.sep}', prompt)
        self.assertIn(f'  - {doc}', prompt)


class StreamingLaunchCarriesTheBoundaryTests(unittest.TestCase):
    """The CHAT launcher — the one whose session raised the out-of-scope popup.

    Mutation testing showed nothing noticed if it stopped passing its working
    directory: every chat session would silently lose the boundary while every
    other test stayed green.
    """

    def test_the_streaming_launch_command_carries_the_boundary(self) -> None:
        from claude_core_lib.claude_core_lib.session.streaming import (
            StreamingClaudeSession,
        )

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = os.path.realpath(tmp.name)
        task = os.path.join(root, 'PROJ-10')
        repo = os.path.join(task, 'chat-repo')
        os.makedirs(repo)
        doc = os.path.join(root, 'architecture.md')
        with open(doc, 'w', encoding='utf-8') as handle:
            handle.write('# Architecture\n')

        session = StreamingClaudeSession(
            task_id='PROJ-10', cwd=repo, architecture_doc_path=doc,
        )
        with patch.dict(os.environ, {WORKSPACES_ROOT_ENV: root}), patch(
            'shutil.which', return_value='/usr/local/bin/claude',
        ):
            command = session._build_command()
        self.assertIn('--append-system-prompt', command)
        prompt = command[command.index('--append-system-prompt') + 1]
        self.assertIn(f'YOUR TASK FOLDER IS: {task}', prompt)
        self.assertIn(f'YOUR MEMORY IS ONLY IN: {os.path.join(task, "memory")}{os.sep}', prompt)
        self.assertIn(f'  - {doc}', prompt)


if __name__ == '__main__':
    unittest.main()
