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


if __name__ == '__main__':
    unittest.main()
