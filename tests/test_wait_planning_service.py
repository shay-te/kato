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

    def _spawn(self, tags, context=None):
        from unittest.mock import Mock
        from kato_core_lib.data_layers.service.wait_planning_service import (
            _PlanningContext,
        )
        manager = Mock()
        manager.get_session.return_value = None
        runner = Mock()
        service = WaitPlanningService(
            session_manager=manager,
            repository_service=Mock(**{'resolve_task_repositories.return_value': []}),
            task_state_service=Mock(),
            planning_session_runner=runner,
        )
        service._spawn_planning_session(
            build_task(tags=tags),
            context or _PlanningContext(cwd='/w', expected_branch='b'),
            WaitPlanningService._hold_mode(build_task(tags=tags)),
        )
        # The runner's funnel — NOT ``session_manager.start_session``. Spawning
        # around the funnel is what dropped the sandbox root, the --add-dir
        # set, the plan-mode lock and the per-task backend defaults.
        manager.start_session.assert_not_called()
        return runner.start_session.call_args.kwargs

    def test_wait_planning_pins_plan_mode(self) -> None:
        self.assertEqual(self._spawn(['kato:wait-planning'])['permission_mode'], 'plan')

    def test_wait_editing_does_not_pin_plan_mode(self) -> None:
        # Forcing ``plan`` here would recreate the plan-then-work latency the
        # tag exists to remove. Empty ⇒ the runner applies the per-task lock
        # and then its configured default.
        self.assertEqual(self._spawn(['kato:wait-editing'])['permission_mode'], '')

    def test_hold_spawn_scopes_the_sandbox_to_the_task_folder(self) -> None:
        """cwd is ONE repo clone; mounting it hides the task's other repos."""
        from kato_core_lib.data_layers.service.wait_planning_service import (
            _PlanningContext,
        )
        kwargs = self._spawn(
            ['kato:wait-editing'],
            _PlanningContext(
                cwd='/ws/UNA-1/client',
                expected_branch='UNA-1',
                workspace_root='/ws/UNA-1',
                repository_paths=('/ws/UNA-1/client', '/ws/UNA-1/server'),
                additional_dirs=('/ws/UNA-1',),
            ),
        )
        self.assertEqual(kwargs['workspace_root'], '/ws/UNA-1')
        self.assertEqual(kwargs['additional_dirs'], ['/ws/UNA-1'])
        self.assertEqual(kwargs['cwd'], '/ws/UNA-1/client')
        self.assertEqual(kwargs['branch_name'], 'UNA-1')

    def test_hold_spawn_without_a_runner_is_reported_not_silently_wrong(self) -> None:
        from unittest.mock import Mock
        from kato_core_lib.data_layers.service.wait_planning_service import (
            _PlanningContext,
        )
        manager = Mock()
        service = WaitPlanningService(
            session_manager=manager,
            repository_service=Mock(),
            task_state_service=Mock(),
        )
        # ``_spawn_planning_session`` swallows + logs, so assert on the
        # raising helper directly.
        with self.assertRaises(RuntimeError):
            service._start_hold_session(
                build_task(), _PlanningContext(cwd='/w', expected_branch='b'), 'go', '',
            )


class HoldPromptWorkspaceScopeTests(unittest.TestCase):
    """Every hold prompt must NAME the task folder it opens on.

    The operator had to paste the clone path into the chat before the agent
    could act: the hold prompts were the only spawn surface with no boundary
    block, so the agent guessed a path, found nothing, and asked.
    """

    def _context(self):
        from kato_core_lib.data_layers.service.wait_planning_service import (
            _PlanningContext,
        )
        return _PlanningContext(
            cwd='/ws/UNA-1/client',
            expected_branch='UNA-1',
            workspace_root='/ws/UNA-1',
            repository_paths=('/ws/UNA-1/client', '/ws/UNA-1/server'),
            additional_dirs=('/ws/UNA-1',),
        )

    def test_editing_prompt_names_the_task_folder_and_lists_the_repos(self) -> None:
        prompt = WaitPlanningService._build_editing_prompt(build_task(), self._context())
        self.assertIn('YOUR TASK FOLDER IS: /ws/UNA-1', prompt)
        self.assertIn('Repositories available in this workspace', prompt)
        self.assertIn('/ws/UNA-1/client', prompt)
        self.assertIn('/ws/UNA-1/server', prompt)

    def test_planning_prompt_names_the_task_folder(self) -> None:
        prompt = WaitPlanningService._build_planning_prompt(build_task(), self._context())
        self.assertIn('YOUR TASK FOLDER IS: /ws/UNA-1', prompt)

    def test_boundary_comes_before_the_untrusted_ticket_text(self) -> None:
        """A rule under three paragraphs of attacker-writable text is not a rule."""
        prompt = WaitPlanningService._build_editing_prompt(build_task(), self._context())
        self.assertLess(
            prompt.index('YOUR TASK FOLDER IS'), prompt.index('Task definition'),
        )

    def test_editing_prompt_never_claims_it_is_owed_a_clone_directory(self) -> None:
        prompt = WaitPlanningService._build_editing_prompt(build_task(), self._context())
        self.assertNotIn('clone directory to work in', prompt)


if __name__ == '__main__':
    unittest.main()
