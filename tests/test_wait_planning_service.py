import unittest
from unittest.mock import patch

from kato_core_lib.data_layers.service.wait_planning_service import WaitPlanningService
from tests.utils import build_task


class WaitPlanningServicePromptTests(unittest.TestCase):
    def test_planning_prompt_marks_ignored_repositories_out_of_bounds(self) -> None:
        with patch.dict(
            'os.environ',
            {'AGENT_IGNORED_REPOSITORY_FOLDERS': 'secret-client'},
        ):
            prompt = WaitPlanningService._build_planning_prompt(build_task())

        self.assertIn('Forbidden repository folders', prompt)
        self.assertIn('- secret-client', prompt)
        self.assertIn('Do not access them with Read, Glob, Grep, Bash', prompt)
        self.assertIn('Execution protocol for forbidden repositories', prompt)
        self.assertIn('DO NOT call any tools', prompt)


class WaitPlanningTagDetectionTests(unittest.TestCase):
    def test_task_with_unrelated_tags_is_not_wait_planning(self) -> None:
        # Branch 85->84: an unrelated tag is encountered, the inner ``if``
        # is False, and the loop continues to the next iteration before
        # eventually falling through to ``return False``.
        task = build_task(tags=['kato:triage:high', 'other-tag', ''])
        self.assertFalse(WaitPlanningService.task_has_wait_planning_tag(task))

    def test_task_with_wait_planning_tag_returns_true(self) -> None:
        task = build_task(tags=['unrelated', 'kato:wait-planning'])
        self.assertTrue(WaitPlanningService.task_has_wait_planning_tag(task))


class WaitEditingTagDetectionTests(unittest.TestCase):
    def test_wait_editing_tag_is_detected(self) -> None:
        task = build_task(tags=['unrelated', 'kato:wait-editing'])
        self.assertTrue(WaitPlanningService.task_has_wait_editing_tag(task))
        self.assertFalse(WaitPlanningService.task_has_wait_planning_tag(task))

    def test_neither_tag_means_no_hold(self) -> None:
        self.assertEqual(
            WaitPlanningService._hold_mode(build_task(tags=['kato:triage:high'])),
            '',
        )

    def test_each_tag_selects_its_own_mode(self) -> None:
        self.assertEqual(
            WaitPlanningService._hold_mode(build_task(tags=['kato:wait-planning'])),
            'planning',
        )
        self.assertEqual(
            WaitPlanningService._hold_mode(build_task(tags=['kato:wait-editing'])),
            'editing',
        )

    def test_planning_wins_when_both_tags_are_present(self) -> None:
        """The stricter hold must win — never silently allow edits."""
        task = build_task(tags=['kato:wait-editing', 'kato:wait-planning'])
        self.assertEqual(WaitPlanningService._hold_mode(task), 'planning')


class WaitEditingPromptTests(unittest.TestCase):
    def _prompt(self) -> str:
        return WaitPlanningService._build_editing_prompt(
            build_task(summary='Fix the FOC rule', description='It rejects valid input.'),
        )

    def test_carries_the_task_definition_framed_as_untrusted(self) -> None:
        prompt = self._prompt()
        self.assertIn('Task definition', prompt)
        self.assertIn('Fix the FOC rule', prompt)
        self.assertIn('It rejects valid input.', prompt)
        # Tracker text is attacker-writable; it must never look like kato's
        # own scaffolding to the model.
        self.assertIn('UNTRUSTED_WORKSPACE_FILE', prompt)

    def test_forbids_planning_and_parks_until_the_go_ahead(self) -> None:
        prompt = self._prompt()
        self.assertIn('Do not produce a plan', prompt)
        self.assertIn('Do not start yet', prompt)
        self.assertIn('go-ahead', prompt)

    def test_does_not_inherit_the_planning_only_tool_ban(self) -> None:
        """wait-editing exists to IMPLEMENT — a blanket tool ban defeats it."""
        prompt = self._prompt()
        self.assertNotIn('DO NOT call any tools', prompt)
        self.assertNotIn('planning-only', prompt)

    def test_still_carries_the_done_sentinel_contract(self) -> None:
        self.assertIn('KATO_TASK_DONE', self._prompt())


class HoldSpawnModeTests(unittest.TestCase):
    """Only wait-planning may pin ``--permission-mode plan``."""

    def _spawn(self, tags):
        from types import SimpleNamespace
        from unittest.mock import Mock
        manager = Mock()
        manager.get_session.return_value = None
        service = WaitPlanningService(
            session_manager=manager,
            repository_service=Mock(**{'resolve_task_repositories.return_value': []}),
            task_state_service=Mock(),
        )
        service._spawn_planning_session(
            build_task(tags=tags),
            SimpleNamespace(cwd='/w', expected_branch='b'),
            WaitPlanningService._hold_mode(build_task(tags=tags)),
        )
        return manager.start_session.call_args.kwargs

    def test_wait_planning_pins_plan_mode(self) -> None:
        self.assertEqual(self._spawn(['kato:wait-planning'])['permission_mode'], 'plan')

    def test_wait_editing_does_not_pin_plan_mode(self) -> None:
        # Forcing ``plan`` here would recreate the plan-then-work latency the
        # tag exists to remove.
        self.assertNotIn('permission_mode', self._spawn(['kato:wait-editing']))


if __name__ == '__main__':
    unittest.main()
