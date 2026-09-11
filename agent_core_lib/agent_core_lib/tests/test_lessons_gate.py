"""The lessons gate — every tool blocked until the lessons file is read.

The lessons text used to be pasted into the appended system prompt, which is
both what made the agent read it and what broke the spawn: ~37.6K characters
of command line against a hard Windows limit of 32,767, so the CLI never
started at all ("[WinError 206] The filename or extension is too long").

The prompt now names the path. That turns "the agent has read the lessons"
from a guarantee into a hope, which is what this hook exists to close.

Everything here fails OPEN. A hook that can wedge itself into denying every
tool costs the whole task; a hook that misses costs one unread file.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_core_lib.agent_core_lib.helpers.lessons_gate import (
    LESSONS_PATH_ENV,
    STATE_DIR_ENV,
    decide,
)


class LessonsGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.lessons = self.tmp / 'lessons.md'
        self.lessons.write_text('- always use logger\n', encoding='utf-8')
        self.state = self.tmp / 'state'

    def _env(self, **overrides):
        env = {
            LESSONS_PATH_ENV: str(self.lessons),
            STATE_DIR_ENV: str(self.state),
        }
        env.update(overrides)
        return patch.dict(os.environ, env)

    @staticmethod
    def _call(tool_name, session_id='sess-1', tool_input=None):
        return {
            'tool_name': tool_name,
            'session_id': session_id,
            'tool_input': tool_input or {},
        }

    def _deny_reason(self, decision) -> str:
        self.assertIsNotNone(decision, 'expected a deny decision')
        out = decision['hookSpecificOutput']
        self.assertEqual(out['permissionDecision'], 'deny')
        return out['permissionDecisionReason']

    def test_a_tool_is_blocked_before_the_lessons_are_read(self) -> None:
        with self._env():
            reason = self._deny_reason(decide(self._call('Bash')))
        # The denial has to be actionable: it names the exact path to read.
        self.assertIn(str(self.lessons), reason)
        self.assertIn('Read', reason)

    def test_reading_the_lessons_unblocks_everything_after_it(self) -> None:
        with self._env():
            self.assertIsNone(decide(self._call(
                'Read', tool_input={'file_path': str(self.lessons)},
            )))
            self.assertIsNone(decide(self._call('Bash')))
            self.assertIsNone(decide(self._call('Edit')))

    def test_Read_itself_is_never_blocked(self) -> None:
        # Gating Read would be a deadlock: it is the only way to satisfy the
        # gate.
        with self._env():
            self.assertIsNone(decide(self._call(
                'Read', tool_input={'file_path': '/somewhere/else.py'},
            )))

    def test_reading_a_DIFFERENT_file_does_not_satisfy_the_gate(self) -> None:
        with self._env():
            decide(self._call('Read', tool_input={'file_path': '/other.md'}))
            self.assertIsNotNone(decide(self._call('Bash')))

    def test_the_path_need_not_match_character_for_character(self) -> None:
        # The agent echoes back whatever form it was handed; the gate must not
        # hinge on spelling.
        messy = str(self.tmp / 'sub' / '..' / 'lessons.md')
        with self._env():
            decide(self._call('Read', tool_input={'file_path': messy}))
            self.assertIsNone(decide(self._call('Bash')))

    def test_a_RESUMED_session_stays_unblocked(self) -> None:
        # The marker is on disk and keyed by session id, so ``--resume`` picks
        # up where it left off instead of re-gating a session mid-task.
        with self._env():
            decide(self._call(
                'Read', session_id='resumed-1',
                tool_input={'file_path': str(self.lessons)},
            ))
        with self._env():
            self.assertIsNone(decide(self._call('Bash', session_id='resumed-1')))

    def test_a_DIFFERENT_session_is_still_gated(self) -> None:
        with self._env():
            decide(self._call(
                'Read', session_id='sess-a',
                tool_input={'file_path': str(self.lessons)},
            ))
            self.assertIsNotNone(decide(self._call('Bash', session_id='sess-b')))

    def test_no_lessons_path_configured_means_no_gate(self) -> None:
        # Matches ``read_lessons_file``, which injects no directive for a
        # missing or blank file. A gate with no directive would deny every
        # tool while nothing on screen said why.
        with self._env(**{LESSONS_PATH_ENV: ''}):
            self.assertIsNone(decide(self._call('Bash')))

    def test_no_state_dir_means_no_gate(self) -> None:
        # The marker could never be written, so gating would deny every tool
        # for the whole session. Fail open.
        with self._env(**{STATE_DIR_ENV: ''}):
            self.assertIsNone(decide(self._call('Bash')))

    def test_a_session_with_no_id_is_not_gated(self) -> None:
        with self._env():
            self.assertIsNone(decide(self._call('Bash', session_id='')))

    def test_the_agent_can_still_ask_a_human_or_leave_plan_mode(self) -> None:
        # Blocking these turns the gate into a hang rather than a nudge, and
        # none of them touch the codebase.
        with self._env():
            for tool in ('AskUserQuestion', 'ExitPlanMode', 'TodoWrite'):
                with self.subTest(tool=tool):
                    self.assertIsNone(decide(self._call(tool)))

    def test_an_unwritable_state_dir_does_not_wedge_the_session(self) -> None:
        with self._env(), patch(
            'agent_core_lib.agent_core_lib.helpers.lessons_gate.open',
            side_effect=OSError('read-only'),
        ):
            # Recording the read fails silently...
            self.assertIsNone(decide(self._call(
                'Read', tool_input={'file_path': str(self.lessons)},
            )))
            # ...and the next tool is asked again rather than crashing.
            self.assertIsNotNone(decide(self._call('Bash')))


if __name__ == '__main__':
    unittest.main()
