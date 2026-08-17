"""The composer's Explain mode: answer the question, change nothing.

Explain is the only composer mode that is not a CLI permission mode, so the
tests below pin the two things that make it work — the spawn RESOLUTION (a
permission mode plus a read-only tool split) and the fact that the restriction
is enforced by tool denial rather than by the prompt.
"""

import unittest
from types import SimpleNamespace

from agent_core_lib.agent_core_lib.helpers.read_only_tools import (
    READ_ONLY_ALLOWED_TOOLS,
    READ_ONLY_DISALLOWED_TOOLS,
    is_read_only_tool_set,
)
from kato_core_lib.helpers.explain_mode_utils import (
    EXPLAIN_MODE,
    EXPLAIN_PERMISSION_MODE,
    explain_prompt,
    is_explain_mode,
    resolve_explain_spawn,
    session_is_in_explain_mode,
)


class ExplainModeDetectionTests(unittest.TestCase):
    def test_recognizes_the_composer_token(self) -> None:
        self.assertTrue(is_explain_mode('explain'))
        self.assertTrue(is_explain_mode('  Explain  '))

    def test_other_modes_are_not_explain(self) -> None:
        for mode in ('', 'default', 'acceptEdits', 'plan', 'bypassPermissions', None):
            self.assertFalse(is_explain_mode(mode), mode)


class ExplainSpawnResolutionTests(unittest.TestCase):
    def test_resolves_to_a_real_permission_mode_plus_read_only_tools(self) -> None:
        spawn = resolve_explain_spawn(EXPLAIN_MODE)
        # 'explain' is not a CLI mode — handing it to --permission-mode would
        # break the spawn, so it must resolve to a real one.
        self.assertEqual(spawn['permission_mode'], EXPLAIN_PERMISSION_MODE)
        self.assertIn(spawn['permission_mode'], ('default', 'acceptEdits', 'plan'))
        self.assertEqual(spawn['disallowed_tools'], READ_ONLY_DISALLOWED_TOOLS)
        self.assertEqual(spawn['allowed_tools'], READ_ONLY_ALLOWED_TOOLS)

    def test_does_not_resolve_to_plan_mode(self) -> None:
        """Explain exists precisely BECAUSE plan mode produces a plan."""
        self.assertNotEqual(resolve_explain_spawn(EXPLAIN_MODE)['permission_mode'], 'plan')

    def test_every_mutating_tool_is_denied(self) -> None:
        denied = resolve_explain_spawn(EXPLAIN_MODE)['disallowed_tools'].lower()
        for tool in ('edit', 'write', 'multiedit', 'notebookedit', 'bash'):
            self.assertIn(tool, denied)

    def test_inspection_tools_survive(self) -> None:
        """A mode that can't read anything can't answer anything."""
        allowed = resolve_explain_spawn(EXPLAIN_MODE)['allowed_tools'].lower()
        for tool in ('read', 'glob', 'grep'):
            self.assertIn(tool, allowed)

    def test_other_modes_get_empty_overrides(self) -> None:
        # Empty means "don't override" — a non-Explain spawn must keep the
        # configured defaults untouched.
        for mode in ('', 'plan', 'bypassPermissions'):
            self.assertEqual(
                resolve_explain_spawn(mode),
                {'permission_mode': '', 'allowed_tools': '', 'disallowed_tools': ''},
                mode,
            )


class ExplainPromptTests(unittest.TestCase):
    def test_forbids_planning_and_editing_and_keeps_the_question(self) -> None:
        prompt = explain_prompt('what does _hold_mode do?')
        self.assertIn('Do NOT produce a plan', prompt)
        self.assertIn('Do NOT edit', prompt)
        self.assertIn('what does _hold_mode do?', prompt)

    def test_asks_for_an_answer_proportional_to_the_question(self) -> None:
        self.assertIn('a small question gets a short answer', explain_prompt('hi'))

    def test_empty_message_still_yields_the_instruction(self) -> None:
        self.assertIn('ANSWER-ONLY TURN', explain_prompt(''))


class LiveSessionRecognitionTests(unittest.TestCase):
    """The restriction is baked at spawn; a caller must be able to read it back."""

    def test_recognizes_a_read_only_spawn(self) -> None:
        session = SimpleNamespace(disallowed_tools=READ_ONLY_DISALLOWED_TOOLS)
        self.assertTrue(session_is_in_explain_mode(session))

    def test_order_and_extra_denials_do_not_break_recognition(self) -> None:
        session = SimpleNamespace(
            disallowed_tools='WebFetch, Bash ,NotebookEdit,MultiEdit,Write,Edit,SomeOperatorTool',
        )
        self.assertTrue(session_is_in_explain_mode(session))

    def test_a_partial_denylist_is_not_read_only(self) -> None:
        """Missing one mutating tool means the session can still mutate."""
        session = SimpleNamespace(disallowed_tools='Edit,Write')
        self.assertFalse(session_is_in_explain_mode(session))

    def test_unrestricted_session_is_not_explain(self) -> None:
        self.assertFalse(session_is_in_explain_mode(SimpleNamespace(disallowed_tools='')))
        self.assertFalse(session_is_in_explain_mode(SimpleNamespace()))

    def test_helper_is_the_shared_one(self) -> None:
        self.assertTrue(is_read_only_tool_set(READ_ONLY_DISALLOWED_TOOLS))


class RestrictionTakesEffectImmediatelyTests(unittest.TestCase):
    """Selecting Explain mid-turn must actually stop the editing.

    The reported bug: the operator flipped the composer to Explain while
    the agent was working and it kept editing files. The CLI bakes the
    read-only tool denial at SPAWN time, so only a respawn can apply it —
    and the one function that decides to respawn bailed out early on
    ``is_working`` ("don't interrupt a turn") and on attached images.
    Both bail-outs fall through to ``_deliver_to_live_session``, which
    hands the message to the subprocess that still holds every mutating
    tool. The mode change was, in effect, ignored.

    Tightening and loosening are therefore deliberately asymmetric.
    """

    def setUp(self) -> None:
        from unittest.mock import MagicMock
        from kato_webserver import app as app_module

        self.module = app_module
        self.app = MagicMock()
        self.app.config = {'TASK_PLAN_MODE_OVERRIDES': {}}
        self.manager = MagicMock()

    def _session(self, *, working: bool, disallowed: str = '', mode: str = 'default'):
        return SimpleNamespace(
            is_alive=True,
            is_working=working,
            permission_mode=mode,
            disallowed_tools=disallowed,
        )

    def _needs_respawn(self, *, requested, session, images=None):
        self.app.config['TASK_PLAN_MODE_OVERRIDES'] = {'T-1': requested}
        self.manager.get_session.return_value = session
        return self.module._plan_mode_change_needs_respawn(
            self.app, self.manager, 'T-1', images,
        )

    def test_switching_to_explain_mid_turn_respawns(self) -> None:
        # THE BUG: this returned False, so the message went to the live
        # editing subprocess and the agent kept writing files.
        self.assertTrue(self._needs_respawn(
            requested=EXPLAIN_MODE, session=self._session(working=True),
        ))

    def test_switching_to_explain_while_idle_respawns(self) -> None:
        self.assertTrue(self._needs_respawn(
            requested=EXPLAIN_MODE, session=self._session(working=False),
        ))

    def test_attached_images_do_not_defeat_a_restriction(self) -> None:
        # Losing an attachment is strictly better than editing code the
        # operator just asked the agent to stop touching.
        self.assertTrue(self._needs_respawn(
            requested=EXPLAIN_MODE,
            session=self._session(working=True),
            images=[{'media_type': 'image/png', 'data': 'x'}],
        ))

    def test_already_in_explain_does_not_respawn(self) -> None:
        self.assertFalse(self._needs_respawn(
            requested=EXPLAIN_MODE,
            session=self._session(working=True, disallowed=READ_ONLY_DISALLOWED_TOOLS),
        ))

    def test_leaving_explain_waits_for_an_idle_session(self) -> None:
        # Loosening is not urgent: letting a read-only turn finish is
        # harmless, and interrupting it would throw away the answer.
        restricted = self._session(working=True, disallowed=READ_ONLY_DISALLOWED_TOOLS)
        self.assertFalse(self._needs_respawn(requested='', session=restricted))

    def test_leaving_explain_respawns_once_idle(self) -> None:
        idle = self._session(working=False, disallowed=READ_ONLY_DISALLOWED_TOOLS)
        self.assertTrue(self._needs_respawn(requested='', session=idle))

    def test_no_live_session_needs_no_respawn(self) -> None:
        dead = SimpleNamespace(is_alive=False, is_working=False,
                               permission_mode='default', disallowed_tools='')
        self.assertFalse(self._needs_respawn(requested=EXPLAIN_MODE, session=dead))


if __name__ == '__main__':
    unittest.main()
