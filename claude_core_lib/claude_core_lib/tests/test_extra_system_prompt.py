"""The host's standing guidance reaches every launch's system prompt.

A host hands the session manager one ``extra_system_prompt`` string — a rule
that has to hold for the whole task, such as how plans are written and
tracked. It cannot live in the first user message (a resumed session never
receives that, and a long conversation summarises it away), so it rides in
``--append-system-prompt``, which is rebuilt on every launch.

Driven through the real builder, the real streaming command and the real
manager; only the session subprocess is faked.
"""
from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    WORKSPACES_ROOT_ENV,
)
from claude_core_lib.claude_core_lib.helpers.spawn_utils import (
    build_appended_system_prompt,
)
from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
from claude_core_lib.claude_core_lib.session.streaming import (
    StreamingClaudeSession,
)

_LOGGER = logging.getLogger('test-extra-system-prompt')
_GUIDANCE = '# Plans end with a checklist\n- [ ] tick it as you go'


def _task_layout(test: unittest.TestCase) -> tuple[str, str]:
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    root = os.path.realpath(tmp.name)
    repo = os.path.join(root, 'PROJ-1', 'api')
    os.makedirs(repo)
    return root, repo


class BuilderTests(unittest.TestCase):

    def setUp(self) -> None:
        self.root, self.repo = _task_layout(self)

    def _build(self, extra: str) -> str:
        with patch.dict(os.environ, {WORKSPACES_ROOT_ENV: self.root}):
            return build_appended_system_prompt(
                lessons_path='',
                docker_mode_on=False,
                logger=_LOGGER,
                cwd=self.repo,
                extra_system_prompt=extra,
            )

    def test_the_guidance_is_the_last_section(self) -> None:
        prompt = self._build(_GUIDANCE)
        self.assertTrue(prompt.endswith(_GUIDANCE), prompt[-120:])
        # Appended, not substituted: the boundary still leads.
        self.assertTrue(prompt.startswith('# Task folder boundary'), prompt[:80])

    def test_no_guidance_leaves_the_prompt_exactly_as_before(self) -> None:
        self.assertEqual(self._build(''), self._build('   \n  '))
        self.assertNotIn('checklist', self._build(''))

    def test_guidance_alone_is_still_delivered(self) -> None:
        # No task folder, no lessons: the guidance is the whole prompt rather
        # than being dropped along with the empty sections.
        with patch.dict(os.environ, {WORKSPACES_ROOT_ENV: ''}):
            prompt = build_appended_system_prompt(
                lessons_path='', docker_mode_on=False, logger=_LOGGER,
                cwd=self.repo, extra_system_prompt=_GUIDANCE,
            )
        self.assertIn(_GUIDANCE, prompt)
        self.assertNotIn('# Task folder boundary', prompt)


class StreamingLaunchTests(unittest.TestCase):

    def _command(self, session: StreamingClaudeSession, root: str) -> list[str]:
        with patch.dict(os.environ, {WORKSPACES_ROOT_ENV: root}), patch(
            'shutil.which', return_value='/usr/local/bin/claude',
        ):
            return session._build_command()

    def test_every_launch_carries_the_guidance(self) -> None:
        root, repo = _task_layout(self)
        session = StreamingClaudeSession(
            task_id='PROJ-1', cwd=repo, extra_system_prompt=_GUIDANCE,
        )
        # A respawn rebuilds the command from the same session state; the
        # second build stands in for it.
        for command in (self._command(session, root), self._command(session, root)):
            prompt = command[command.index('--append-system-prompt') + 1]
            self.assertTrue(prompt.endswith(_GUIDANCE), prompt[-120:])

    def test_a_resumed_launch_carries_the_guidance(self) -> None:
        root, repo = _task_layout(self)
        session = StreamingClaudeSession(
            task_id='PROJ-1', cwd=repo, resume_session_id='sess-old',
            extra_system_prompt=_GUIDANCE,
        )
        command = self._command(session, root)
        self.assertIn('--resume', command)
        prompt = command[command.index('--append-system-prompt') + 1]
        self.assertIn(_GUIDANCE, prompt)


class _FakeSession:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.task_id = kwargs['task_id']
        self._session_id = kwargs.get('resume_session_id') or 'sess-1'

    cwd = property(lambda self: self.kwargs.get('cwd', ''))
    agent_session_id = property(lambda self: self._session_id)
    is_alive = property(lambda self: True)

    def start(self, initial_prompt: str = '') -> None:
        pass

    def terminate(self) -> None:
        pass

    def stderr_snapshot(self) -> list[str]:
        return []


class ManagerTests(unittest.TestCase):

    def test_the_manager_hands_the_guidance_to_the_session(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        made: list[_FakeSession] = []

        def factory(**kwargs):
            made.append(_FakeSession(**kwargs))
            return made[-1]

        manager = ClaudeSessionManager(
            state_dir=Path(tmp.name), session_factory=factory,
        )
        manager.start_session(task_id='PROJ-2', extra_system_prompt=_GUIDANCE)
        self.assertEqual(made[-1].kwargs['extra_system_prompt'], _GUIDANCE)

    def test_no_guidance_is_an_empty_string_not_a_missing_kwarg(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        made: list[_FakeSession] = []

        def factory(**kwargs):
            made.append(_FakeSession(**kwargs))
            return made[-1]

        manager = ClaudeSessionManager(
            state_dir=Path(tmp.name), session_factory=factory,
        )
        manager.start_session(task_id='PROJ-3')
        self.assertEqual(made[-1].kwargs['extra_system_prompt'], '')


if __name__ == '__main__':
    unittest.main()
