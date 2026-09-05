"""Plan mode must not be edited around.

Plan mode passes only ``--permission-mode plan``. It denies no tools, so the
permission prompt IS the whole enforcement — and kato auto-resolves prompts
from remembered decisions before they are ever shown.

A remembered decision for a non-Bash tool is stored under the bare tool name,
which makes it GLOBAL across tasks and durable across restarts. So one
earlier "Allow always" on ``Edit``, clicked on any task at any time, silently
took every later plan-locked session out of plan mode: the agent edited, no
prompt appeared, and nothing on screen explained it.

Reported as "he is start to change in plan mode", and again as "remember that
claude/codex/agent will not start writing code in plan mode".

``ExitPlanMode`` was already carved out. These tests cover the other half:
the mutating tools themselves.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kato_webserver import app as app_module
from kato_webserver.app import create_app


class _Manager:
    def list_records(self):
        return []

    def get_record(self, task_id):  # noqa: ARG002
        return None

    def get_session(self, task_id):  # noqa: ARG002
        return None

    def backend_for(self, task_id):  # noqa: ARG002
        # A REAL session manager answers this. Without it every per-task
        # override key stays bare and the mismatch below is invisible —
        # which is exactly why the first round of these tests passed while
        # the shipped lock did nothing.
        return 'claude'


class PlanModeBlocksMutatingAutoApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(session_manager=_Manager())
        self.app.config['TASK_PLAN_MODE_OVERRIDES'] = {}
        self.session = SimpleNamespace(is_alive=True)

    def _lock_to_plan(self, task_id='T1'):
        self.app.config['TASK_PLAN_MODE_OVERRIDES'][task_id] = 'plan'

    def _auto_resolve(self, tool_name, task_id='T1'):
        """Run the auto-resolver for one pending ask; True = it was approved
        without a human."""
        with patch.object(
            app_module, '_pending_tool',
            return_value=(tool_name, {'file_path': '/w/a.py'}),
        ), patch.object(
            app_module, '_classify_action_for', return_value=None,
        ), patch(
            'kato_core_lib.helpers.tool_decision_store.recall_tool_decision',
            return_value='allow',
        ), patch.object(
            app_module, '_resolve_permission_decision', return_value=None,
        ):
            return app_module._maybe_auto_resolve_pending(
                self.app, self.session, task_id, 'req-1', False,
            )

    def test_a_remembered_allow_does_NOT_auto_approve_Edit_in_plan_mode(self) -> None:
        # THE REPORT.
        self._lock_to_plan()
        self.assertFalse(
            self._auto_resolve('Edit'),
            'an edit was auto-approved inside a plan-locked session',
        )

    def test_every_mutating_tool_is_blocked_not_just_Edit(self) -> None:
        # Write and MultiEdit change files just as much as Edit does; Bash
        # can do anything at all.
        self._lock_to_plan()
        for tool in ('Write', 'MultiEdit', 'NotebookEdit', 'Bash'):
            with self.subTest(tool=tool):
                self.assertFalse(
                    self._auto_resolve(tool),
                    f'{tool} was auto-approved inside a plan-locked session',
                )

    def test_READ_ONLY_tools_still_auto_resolve_in_plan_mode(self) -> None:
        # Planning needs to read widely. Making the operator approve every
        # Grep is how a safety prompt becomes something people click through
        # without looking.
        self._lock_to_plan()
        self.assertTrue(self._auto_resolve('Read'))
        self.assertTrue(self._auto_resolve('Grep'))

    def test_the_block_applies_ONLY_to_the_plan_locked_task(self) -> None:
        # A lock on one task must not freeze approvals everywhere else.
        self._lock_to_plan('T1')
        self.assertFalse(self._auto_resolve('Edit', task_id='T1'))
        self.assertTrue(self._auto_resolve('Edit', task_id='T2'))

    def test_a_task_NOT_in_plan_mode_is_unaffected(self) -> None:
        # The remembered-decision feature still works; this is a carve-out,
        # not a removal.
        self.assertTrue(self._auto_resolve('Edit'))

    def test_ExitPlanMode_stays_blocked_regardless_of_mode(self) -> None:
        # The pre-existing carve-out must survive this change: leaving plan
        # mode is the operator's decision every single time.
        self.assertFalse(self._auto_resolve('ExitPlanMode'))
        self._lock_to_plan()
        self.assertFalse(self._auto_resolve('ExitPlanMode'))

    def test_the_mutating_set_is_derived_not_re_listed(self) -> None:
        # A second hand-written copy of "which tools write" would drift, and
        # the copy that drifted would be the one deciding whether plan mode
        # holds. It comes from the same read-only split Explain uses.
        from agent_core_lib.agent_core_lib.helpers.read_only_tools import (
            READ_ONLY_DISALLOWED_TOOLS,
        )
        expected = {
            name.strip() for name in READ_ONLY_DISALLOWED_TOOLS.split(',')
            if name.strip()
        }
        self.assertEqual(app_module._MUTATING_TOOLS, expected)


class PlanLockIsReadableByEVERYONETests(unittest.TestCase):
    """One task-mode map, one key shape.

    ``_override_key`` appends the active backend, because model and effort
    genuinely are backend-specific. A permission mode is the opposite: it is
    a safety lock, and it has to hold whichever CLI is running.

    Keying it by backend split ONE map into two shapes, because two readers
    already used the bare id — the boot loader (the persisted file only ever
    holds bare ids) and the chat route that actually spawns the agent. So
    ``/agent-mode`` wrote ``UNA-3025::claude``, the spawn read ``UNA-3025``,
    got '', and started an ordinary editing session. The operator saw "Plan"
    in the composer and watched the agent change code.
    """

    def setUp(self) -> None:
        self.app = create_app(session_manager=_Manager())
        self.app.config['TASK_PLAN_MODE_OVERRIDES'] = {}

    def _store(self):
        return self.app.config['TASK_PLAN_MODE_OVERRIDES']

    def test_the_key_carries_no_backend_suffix(self) -> None:
        app_module._set_task_mode_of(self.app, 'UNA-3025', 'plan')
        self.assertEqual(list(self._store().keys()), ['UNA-3025'])

    def test_the_SPAWN_path_sees_the_lock(self) -> None:
        # THE REGRESSION. The chat route reads this map with the bare id and
        # passes the result as --permission-mode; a miss here means the CLI
        # is never told to plan.
        app_module._set_task_mode_of(self.app, 'UNA-3025', 'plan')
        self.assertEqual(self._store().get('UNA-3025', ''), 'plan')

    def test_the_UI_and_the_SPAWN_agree(self) -> None:
        # The two disagreeing is what made this invisible: the composer said
        # Plan while the agent edited.
        app_module._set_task_mode_of(self.app, 'UNA-3025', 'plan')
        self.assertEqual(
            app_module._task_mode_of(self.app, 'UNA-3025'),
            self._store().get('UNA-3025', ''),
        )

    def test_a_lock_restored_from_DISK_is_honoured(self) -> None:
        # Boot loads the persisted file verbatim, and it holds bare ids.
        # Reading with a suffixed key silently released the lock on restart.
        self.app.config['TASK_PLAN_MODE_OVERRIDES'] = {'UNA-3025': 'plan'}
        self.assertEqual(app_module._task_mode_of(self.app, 'UNA-3025'), 'plan')
        self.assertTrue(app_module._task_is_plan_locked(self.app, 'UNA-3025'))

    def test_clearing_the_mode_really_clears_it(self) -> None:
        app_module._set_task_mode_of(self.app, 'UNA-3025', 'plan')
        app_module._set_task_mode_of(self.app, 'UNA-3025', '')
        self.assertEqual(self._store(), {})
        self.assertFalse(app_module._task_is_plan_locked(self.app, 'UNA-3025'))

    def test_model_and_effort_KEEP_their_backend_suffix(self) -> None:
        # The carve-out is only for the safety lock. Model and effort must
        # stay backend-scoped or a task switched to Codex inherits Claude's
        # model and the first message dies before it reaches the model.
        self.app.config['TASK_MODEL_OVERRIDES'] = {}
        app_module._set_task_override(
            self.app, 'TASK_MODEL_OVERRIDES', 'UNA-3025', 'opus',
        )
        self.assertEqual(
            list(self.app.config['TASK_MODEL_OVERRIDES'].keys()),
            ['UNA-3025::claude'],
        )


if __name__ == '__main__':
    unittest.main()
