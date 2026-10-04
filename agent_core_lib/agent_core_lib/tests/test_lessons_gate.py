"""The lessons gate — every tool blocked until the lessons file is read, in full.

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

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_core_lib.agent_core_lib.helpers.lessons_gate import (
    LESSONS_PATH_ENV,
    STATE_DIR_ENV,
    decide,
    main,
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
            'hook_event_name': 'PreToolUse',
            'tool_name': tool_name,
            'session_id': session_id,
            'tool_input': tool_input or {},
        }

    def _read(self, start=1, lines=1, total=1, *, session_id='sess-1', path=None,
              succeeds=True):
        """One ``Read`` of the lessons file, as the CLI reports it.

        The request first (``PreToolUse``); then, only when the read worked,
        its result (``PostToolUse``) carrying the lines that came back. The
        shape is the real CLI's, captured from a live run.
        """
        target = str(path or self.lessons)
        tool_input = {'file_path': target, 'offset': start, 'limit': lines}
        decide(self._call('Read', session_id=session_id, tool_input=tool_input))
        if not succeeds:
            return
        decide({
            'hook_event_name': 'PostToolUse',
            'tool_name': 'Read',
            'session_id': session_id,
            'tool_input': tool_input,
            'tool_response': {'type': 'text', 'file': {
                'filePath': target, 'content': '...',
                'numLines': lines, 'startLine': start, 'totalLines': total,
            }},
        })

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
            self._read(1, 1, 1)
            self.assertIsNone(decide(self._call('Bash')))
            self.assertIsNone(decide(self._call('Edit')))

    def test_ASKING_to_read_is_not_reading(self) -> None:
        # The gate used to open on the request, before the read had run — so
        # a read that then FAILED (a file over the tool's size limit fails
        # outright unless it is paged) counted as having read it.
        with self._env():
            self._read(succeeds=False)
            self.assertIsNotNone(decide(self._call('Bash')))

    def test_the_first_page_of_a_long_file_does_not_open_the_gate(self) -> None:
        with self._env():
            self._read(1, 400, 1000)
            reason = self._deny_reason(decide(self._call('Bash')))
        # ...and the denial says exactly where to carry on from.
        self.assertIn('Lines 1-400 of 1000', reason)
        self.assertIn('offset=401', reason)

    def test_the_gate_opens_once_every_line_has_been_returned(self) -> None:
        with self._env():
            self._read(1, 400, 1000)
            self._read(401, 400, 1000)
            self.assertIsNotNone(decide(self._call('Bash')))
            self._read(801, 200, 1000)
            self.assertIsNone(decide(self._call('Bash')))

    def test_pages_may_arrive_in_any_order_and_overlap(self) -> None:
        with self._env():
            self._read(601, 400, 1000)
            self._read(1, 350, 1000)
            self.assertIsNotNone(decide(self._call('Bash')))
            self._read(300, 350, 1000)   # overlaps both neighbours
            self.assertIsNone(decide(self._call('Bash')))

    def test_a_gap_in_the_middle_is_not_coverage(self) -> None:
        with self._env():
            self._read(1, 300, 1000)
            self._read(701, 300, 1000)
            reason = self._deny_reason(decide(self._call('Bash')))
        self.assertIn('offset=301', reason)

    def test_skipping_the_start_is_not_coverage(self) -> None:
        with self._env():
            self._read(501, 500, 1000)
            reason = self._deny_reason(decide(self._call('Bash')))
        # Nothing from line 1 yet, so it is told to start, not to "continue".
        self.assertIn('Read the lessons file first', reason)

    def test_a_file_that_grew_while_being_paged_must_be_read_to_its_new_end(self) -> None:
        with self._env():
            self._read(1, 400, 800)
            self._read(401, 400, 900)   # 100 lines were appended meanwhile
            self.assertIsNotNone(decide(self._call('Bash')))
            self._read(801, 100, 900)
            self.assertIsNone(decide(self._call('Bash')))

    def test_once_read_it_stays_read_even_if_the_file_changes(self) -> None:
        # The agent edits this file at the end of its task; that must not
        # lock it out of the tools it needs to finish.
        with self._env():
            self._read(1, 10, 10)
            self.assertIsNone(decide(self._call('Bash')))
            self._read(1, 5, 500)       # a later, partial look at a longer file
            self.assertIsNone(decide(self._call('Bash')))

    def test_a_result_the_gate_cannot_parse_covers_nothing(self) -> None:
        with self._env():
            for response in (None, 'text', {'file': None}, {'file': {'numLines': 'x'}},
                             {'file': {'startLine': 1, 'numLines': 0, 'totalLines': 9}}):
                decide({
                    'hook_event_name': 'PostToolUse', 'tool_name': 'Read',
                    'session_id': 'sess-1',
                    'tool_input': {'file_path': str(self.lessons)},
                    'tool_response': response,
                })
            self.assertIsNotNone(decide(self._call('Bash')))

    def test_Read_itself_is_never_blocked(self) -> None:
        # Gating Read would be a deadlock: it is the only way to satisfy the
        # gate.
        with self._env():
            self.assertIsNone(decide(self._call(
                'Read', tool_input={'file_path': '/somewhere/else.py'},
            )))

    def test_reading_a_DIFFERENT_file_does_not_satisfy_the_gate(self) -> None:
        with self._env():
            self._read(1, 1, 1, path='/other.md')
            self.assertIsNotNone(decide(self._call('Bash')))

    def test_the_path_need_not_match_character_for_character(self) -> None:
        # The agent echoes back whatever form it was handed; the gate must not
        # hinge on spelling.
        messy = str(self.tmp / 'sub' / '..' / 'lessons.md')
        with self._env():
            self._read(1, 1, 1, path=messy)
            self.assertIsNone(decide(self._call('Bash')))

    def test_a_RESUMED_session_stays_unblocked(self) -> None:
        # The record is on disk and keyed by session id, so ``--resume`` picks
        # up where it left off instead of re-gating a session mid-task.
        with self._env():
            self._read(1, 1, 1, session_id='resumed-1')
        with self._env():
            self.assertIsNone(decide(self._call('Bash', session_id='resumed-1')))

    def test_a_RESUMED_session_keeps_the_pages_it_already_read(self) -> None:
        with self._env():
            self._read(1, 400, 800, session_id='resumed-2')
        with self._env():
            self._read(401, 400, 800, session_id='resumed-2')
            self.assertIsNone(decide(self._call('Bash', session_id='resumed-2')))

    def test_a_DIFFERENT_session_is_still_gated(self) -> None:
        with self._env():
            self._read(1, 1, 1, session_id='sess-a')
            self.assertIsNotNone(decide(self._call('Bash', session_id='sess-b')))

    def test_no_lessons_path_configured_means_no_gate(self) -> None:
        # Matches ``read_lessons_file``, which injects no directive for a
        # missing or blank file. A gate with no directive would deny every
        # tool while nothing on screen said why.
        with self._env(**{LESSONS_PATH_ENV: ''}):
            self.assertIsNone(decide(self._call('Bash')))

    def test_no_state_dir_means_no_gate(self) -> None:
        # The record could never be written, so gating would deny every tool
        # for the whole session. Fail open.
        with self._env(**{STATE_DIR_ENV: ''}):
            self.assertIsNone(decide(self._call('Bash')))
            self._read(1, 1, 1)          # nowhere to record it; must not raise

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

    # ----- never a wedge -----

    def test_reads_that_are_never_reported_back_do_not_hold_the_session(self) -> None:
        # The result event is not wired on this CLI, or every attempt fails.
        # More denying fixes neither; after a few it stands down.
        with self._env():
            denials = 0
            for _ in range(10):
                self._read(succeeds=False)
                if decide(self._call('Bash')) is None:
                    break
                denials += 1
            self.assertEqual(denials, 3)
            # ...and stays down for the rest of the session.
            self.assertIsNone(decide(self._call('Edit')))

    def test_an_agent_that_never_even_tries_is_held_longer(self) -> None:
        with self._env():
            denials = 0
            for _ in range(20):
                if decide(self._call('Bash')) is None:
                    break
                denials += 1
        self.assertEqual(denials, 8)

    def test_a_file_that_can_never_be_finished_does_not_hold_the_session(self) -> None:
        # One line over the read tool's size limit can never be returned.
        with self._env():
            self._read(1, 400, 1000)
            denials = 0
            for _ in range(20):
                if decide(self._call('Bash')) is None:
                    break
                denials += 1
        self.assertEqual(denials, 8)

    def test_an_unwritable_state_dir_does_not_wedge_the_session(self) -> None:
        # A denial that cannot be counted can never run out, and a record that
        # cannot be written can never show the file as read: stand down. This
        # used to deny on every call, for the whole session.
        with self._env(), patch(
            'agent_core_lib.agent_core_lib.helpers.lessons_gate.open',
            side_effect=OSError('read-only'),
        ):
            self._read(1, 1, 1)          # recording it fails silently
            self.assertIsNone(decide(self._call('Bash')))

    def test_a_corrupt_record_is_treated_as_no_record(self) -> None:
        with self._env():
            self.state.mkdir(parents=True, exist_ok=True)
            (self.state / 'lessons-gate-sess-1.json').write_text('[1, 2', encoding='utf-8')
            self.assertIsNotNone(decide(self._call('Bash')))
            (self.state / 'lessons-gate-sess-1.json').write_text('[1, 2]', encoding='utf-8')
            self.assertIsNotNone(decide(self._call('Bash')))


class LessonsGateEntryPointTests(unittest.TestCase):
    """``python -m ...lessons_gate``: always exit 0, whatever arrives."""

    def _run(self, stdin: str, decision=None, raises: Exception | None = None):
        out = io.StringIO()
        with patch('sys.stdin', io.StringIO(stdin)), patch('sys.stdout', out), patch(
            'agent_core_lib.agent_core_lib.helpers.lessons_gate.decide',
            side_effect=raises, return_value=decision,
        ):
            code = main()
        return code, out.getvalue()

    def test_prints_a_deny_decision(self) -> None:
        code, printed = self._run('{}', decision={'hookSpecificOutput': {}})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(printed), {'hookSpecificOutput': {}})

    def test_prints_nothing_when_allowing(self) -> None:
        self.assertEqual(self._run('{}'), (0, ''))

    def test_garbage_on_stdin_is_not_an_error(self) -> None:
        self.assertEqual(self._run('not json'), (0, ''))

    def test_a_crash_while_deciding_is_not_an_error(self) -> None:
        self.assertEqual(self._run('{}', raises=RuntimeError('boom')), (0, ''))


if __name__ == '__main__':
    unittest.main()
