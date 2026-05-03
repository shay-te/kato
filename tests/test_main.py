import os
import types
import unittest
from unittest.mock import Mock, call, patch


from kato_core_lib.main import (
    _RESUME_CONTINUE_PROMPT,
    _RESUME_WAIT_PROMPT,
    _resume_prompt_for_workspace,
    _resume_streaming_sessions,
    _run_task_scan_loop,
    main,
)
from utils import build_test_cfg


class MainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = build_test_cfg()
        self._env_patch = patch.dict(
            'os.environ',
            {
                'KATO_IGNORED_REPOSITORY_FOLDERS': '',
                # OG4 — TLS pin validator is now strict-by-default in
                # main(). Existing tests don't exercise pinning, so
                # they opt out at the test-env level. The dedicated
                # ``MainTlsPinIntegrationTests`` class below locks
                # the actual integration behavior.
                'KATO_SANDBOX_ALLOW_NO_TLS_PIN': 'true',
            },
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_main_returns_zero_on_success(self) -> None:
        app = types.SimpleNamespace(logger=Mock())

        with patch('kato_core_lib.main.validate_environment') as mock_validate_environment, patch(
            'kato_core_lib.main.KatoInstance.init'
        ) as mock_init, patch(
            'kato_core_lib.main.KatoInstance.get',
            return_value=app,
        ), patch('kato_core_lib.main._run_task_scan_loop') as mock_run_loop:
            result = main(self.cfg)

        self.assertEqual(result, 0)
        mock_validate_environment.assert_called_once_with(mode='all')
        mock_init.assert_called_once_with(self.cfg)
        mock_run_loop.assert_called_once_with(
            app,
            startup_delay_seconds=30.0,
            scan_interval_seconds=60.0,
        )
        app.logger.info.assert_any_call('Starting kato agent')

    def test_main_configures_logger_when_app_logger_is_missing(self) -> None:
        configured_logger = Mock()
        app = types.SimpleNamespace(logger=None)

        with patch('kato_core_lib.main.validate_environment'), patch(
            'kato_core_lib.main.configure_logger', return_value=configured_logger
        ), patch(
            'kato_core_lib.main.KatoInstance.init'
        ), patch(
            'kato_core_lib.main.KatoInstance.get',
            return_value=app,
        ), patch('kato_core_lib.main._run_task_scan_loop'):
            main(self.cfg)

        self.assertIs(app.logger, configured_logger)

    def test_run_task_scan_loop_waits_before_first_scan_and_sleeps_between_cycles(self) -> None:
        app = types.SimpleNamespace(logger=Mock())
        job = Mock()
        job.run.side_effect = [None, None]

        with patch('kato_core_lib.main.ProcessAssignedTasksJob', return_value=job) as mock_job_cls, patch(
            'kato_core_lib.main.supports_inline_status',
            return_value=False,
        ), patch('kato_core_lib.main.time.sleep') as mock_sleep:
            _run_task_scan_loop(
                app,
                startup_delay_seconds=30.0,
                scan_interval_seconds=60.0,
                sleep_fn=mock_sleep,
                max_cycles=2,
            )

        mock_job_cls.assert_called_once_with()
        job.initialized.assert_called_once_with(app)
        self.assertEqual(job.run.call_count, 2)
        # The first sleep is the 30s startup delay. After each scan tick
        # the loop divides the 60s scan interval into 5s heartbeat chunks
        # (so the planning UI status bar gets a live countdown). Total
        # sleep between ticks must still sum to 60s.
        sleep_durations = [call_obj.args[0] for call_obj in mock_sleep.call_args_list]
        self.assertEqual(sleep_durations[0], 30.0)
        between_ticks = sleep_durations[1:]
        # 12 chunks of 5s = 60s total. Allow either-or since the loop
        # may emit slightly fewer chunks if the deadline elapses early.
        self.assertAlmostEqual(sum(between_ticks), 60.0, delta=5.0)
        app.logger.info.assert_any_call(
            'Waiting %s before scanning tasks while Kato warms up',
            '30 seconds',
        )

    def test_run_task_scan_loop_uses_warmup_countdown_when_inline_status_is_supported(self) -> None:
        app = types.SimpleNamespace(logger=Mock())
        job = Mock()
        job.run.side_effect = [None]

        with patch('kato_core_lib.main.ProcessAssignedTasksJob', return_value=job), patch(
            'kato_core_lib.main.supports_inline_status',
            return_value=True,
        ), patch(
            'kato_core_lib.main.sleep_with_warmup_countdown'
        ) as mock_warmup_countdown:
            _run_task_scan_loop(
                app,
                startup_delay_seconds=30.0,
                scan_interval_seconds=0.0,
                max_cycles=1,
            )

        mock_warmup_countdown.assert_called_once_with(30.0, sleep_fn=unittest.mock.ANY)
        # Each scan tick now logs the start/end so the planning UI status
        # bar reflects what kato is doing in real time.
        app.logger.info.assert_any_call('Scanning for new tasks and reviews')
        app.logger.info.assert_any_call('Scan complete')

    def test_run_task_scan_loop_continues_after_failure(self) -> None:
        app = types.SimpleNamespace(logger=Mock())
        job = Mock()
        job.run.side_effect = [RuntimeError('service down'), None]

        with patch('kato_core_lib.main.ProcessAssignedTasksJob', return_value=job), patch(
            'kato_core_lib.main.time.sleep'
        ) as mock_sleep:
            _run_task_scan_loop(
                app,
                startup_delay_seconds=0.0,
                scan_interval_seconds=60.0,
                sleep_fn=mock_sleep,
                max_cycles=2,
            )

        self.assertEqual(job.run.call_count, 2)
        app.logger.warning.assert_called_once_with(
            'task scan failed; retrying in %s seconds',
            60.0,
        )

    def test_resume_prompt_continues_interrupted_work_by_default(self) -> None:
        record = types.SimpleNamespace()

        self.assertEqual(_resume_prompt_for_workspace(record), _RESUME_CONTINUE_PROMPT)

    def test_resume_prompt_waits_for_operator_for_planning_workspace(self) -> None:
        record = types.SimpleNamespace(resume_on_startup=False)

        self.assertEqual(_resume_prompt_for_workspace(record), _RESUME_WAIT_PROMPT)

    def test_resume_prompt_includes_forbidden_repository_guardrails(self) -> None:
        record = types.SimpleNamespace()

        with patch.dict(
            'os.environ',
            {'KATO_IGNORED_REPOSITORY_FOLDERS': 'secret-client'},
        ):
            prompt = _resume_prompt_for_workspace(record)

        self.assertIn('Forbidden repository folders', prompt)
        self.assertIn('secret-client', prompt)
        self.assertTrue(prompt.endswith(_RESUME_CONTINUE_PROMPT))

    def test_resume_streaming_sessions_starts_active_workspace_with_continue_prompt(self) -> None:
        workspace_root = types.SimpleNamespace(is_dir=Mock(return_value=True))
        workspace_manager = types.SimpleNamespace(
            list_workspaces=Mock(
                return_value=[
                    types.SimpleNamespace(
                        task_id='PROJ-1',
                        task_summary='continue me',
                        status='active',
                        cwd='',
                        repository_ids=['client'],
                    )
                ]
            ),
            repository_path=Mock(return_value=workspace_root),
        )
        session_manager = types.SimpleNamespace(start_session=Mock())
        app = types.SimpleNamespace(
            logger=Mock(),
            session_manager=session_manager,
            workspace_manager=workspace_manager,
            planning_session_runner=None,
        )

        _resume_streaming_sessions(app)

        session_manager.start_session.assert_called_once()
        call_kwargs = session_manager.start_session.call_args.kwargs
        self.assertEqual(call_kwargs['task_id'], 'PROJ-1')
        self.assertEqual(call_kwargs['initial_prompt'], _RESUME_CONTINUE_PROMPT)
        self.assertEqual(call_kwargs['cwd'], str(workspace_root))

    def test_resume_streaming_sessions_uses_wait_prompt_for_operator_driven_workspace(self) -> None:
        workspace_manager = types.SimpleNamespace(
            list_workspaces=Mock(
                return_value=[
                    types.SimpleNamespace(
                        task_id='PROJ-2',
                        task_summary='planning chat',
                        status='active',
                        cwd='/repo',
                        repository_ids=['client'],
                        resume_on_startup=False,
                    )
                ]
            ),
        )
        session_manager = types.SimpleNamespace(start_session=Mock())
        app = types.SimpleNamespace(
            logger=Mock(),
            session_manager=session_manager,
            workspace_manager=workspace_manager,
            planning_session_runner=None,
        )

        _resume_streaming_sessions(app)

        session_manager.start_session.assert_called_once()
        call_kwargs = session_manager.start_session.call_args.kwargs
        self.assertEqual(call_kwargs['task_id'], 'PROJ-2')
        self.assertEqual(call_kwargs['initial_prompt'], _RESUME_WAIT_PROMPT)

    def test_main_returns_one_without_traceback_when_startup_validation_fails(self) -> None:
        configured_logger = Mock()
        env_error = ValueError('unsupported issue platform: linear')

        with patch('kato_core_lib.main.configure_logger', return_value=configured_logger), patch(
            'kato_core_lib.main.validate_environment',
            side_effect=env_error,
        ), patch(
            'kato_core_lib.main.KatoInstance.init',
        ) as mock_init:
            result = main(self.cfg)

        self.assertEqual(result, 1)
        configured_logger.error.assert_called_once_with('%s', env_error)
        mock_init.assert_not_called()

    def test_docker_mode_on_runs_sandbox_preflight(self) -> None:
        """``KATO_CLAUDE_DOCKER=true`` must run the sandbox daemon checks.

        Locks the Phase 2 gate at ``main.py:86``. If a future refactor
        reverts ``is_docker_mode_enabled()`` back to ``is_bypass_enabled()``,
        ``docker=true, bypass=false`` operators silently lose the docker
        daemon preflight — exactly the case this gate exists to catch.
        """
        app = types.SimpleNamespace(logger=Mock())

        with patch('kato_core_lib.main.validate_environment'), patch(
            'kato_core_lib.main.validate_bypass_permissions'
        ), patch(
            'kato_core_lib.main.print_security_posture'
        ), patch(
            'kato_core_lib.main.KatoInstance.init'
        ), patch(
            'kato_core_lib.main.KatoInstance.get', return_value=app,
        ), patch(
            'kato_core_lib.main._run_task_scan_loop'
        ), patch(
            'kato_core_lib.validation.bypass_permissions_validator.is_docker_mode_enabled',
            return_value=True,
        ), patch(
            'kato_core_lib.sandbox.manager.check_docker_or_exit'
        ) as mock_check_docker, patch(
            'kato_core_lib.sandbox.manager.check_gvisor_or_exit'
        ) as mock_check_gvisor, patch(
            'kato_core_lib.sandbox.manager.gvisor_runtime_available',
            return_value=True,
        ) as mock_gvisor_runtime, patch(
            'kato_core_lib.sandbox.manager.docker_running_rootless',
            return_value=True,
        ) as mock_rootless:
            main(self.cfg)

        mock_check_docker.assert_called_once()
        mock_check_gvisor.assert_called_once()
        mock_gvisor_runtime.assert_called_once()
        mock_rootless.assert_called_once()

    def test_docker_mode_off_skips_sandbox_preflight(self) -> None:
        """``KATO_CLAUDE_DOCKER`` unset → the four sandbox helpers must not run.

        Without this assertion, a regression that runs the sandbox
        preflight unconditionally would force every kato user to install
        Docker even when they're on the host-only path.
        """
        app = types.SimpleNamespace(logger=Mock())

        with patch('kato_core_lib.main.validate_environment'), patch(
            'kato_core_lib.main.validate_bypass_permissions'
        ), patch(
            'kato_core_lib.main.print_security_posture'
        ), patch(
            'kato_core_lib.main.KatoInstance.init'
        ), patch(
            'kato_core_lib.main.KatoInstance.get', return_value=app,
        ), patch(
            'kato_core_lib.main._run_task_scan_loop'
        ), patch(
            'kato_core_lib.validation.bypass_permissions_validator.is_docker_mode_enabled',
            return_value=False,
        ), patch(
            'kato_core_lib.sandbox.manager.check_docker_or_exit'
        ) as mock_check_docker, patch(
            'kato_core_lib.sandbox.manager.check_gvisor_or_exit'
        ) as mock_check_gvisor, patch(
            'kato_core_lib.sandbox.manager.gvisor_runtime_available'
        ) as mock_gvisor_runtime, patch(
            'kato_core_lib.sandbox.manager.docker_running_rootless'
        ) as mock_rootless:
            main(self.cfg)

        mock_check_docker.assert_not_called()
        mock_check_gvisor.assert_not_called()
        mock_gvisor_runtime.assert_not_called()
        mock_rootless.assert_not_called()


class MainTlsPinIntegrationTests(unittest.TestCase):
    """Locks the OG4 wiring: ``main()`` calls the TLS pin validator.

    Without these tests, the validator module exists in isolation and
    the doc claims OG4 is "Closed" — but a refactor that drops the
    validator call from ``main()`` would silently make the protection
    dead code in production. ``MainTests.setUp`` opts the test env
    out of pinning so the unrelated tests don't depend on TLS state;
    THIS class deliberately doesn't opt out and exercises the real
    integration.
    """

    def setUp(self) -> None:
        self.cfg = build_test_cfg()
        # Clear any inherited opt-out so each test below sets the
        # env explicitly. ``main()`` reads the live ``os.environ``.
        self._env_patch = patch.dict(
            'os.environ',
            {'KATO_IGNORED_REPOSITORY_FOLDERS': ''},
            clear=False,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        # Drop both env vars if a previous test or shell set them.
        for key in (
            'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256',
            'KATO_SANDBOX_ALLOW_NO_TLS_PIN',
        ):
            if key in os.environ:
                del os.environ[key]

    def _run_main_with_other_validators_mocked(self) -> int:
        """Run main with everything except the TLS pin validator mocked.

        Lets the test focus on whether the TLS pin validator actually
        fires, without setUp ordering / repository / job mocking
        noise.
        """
        app = types.SimpleNamespace(logger=Mock())
        with patch('kato_core_lib.main.validate_environment'), patch(
            'kato_core_lib.main.validate_bypass_permissions'
        ), patch(
            'kato_core_lib.main.print_security_posture'
        ), patch(
            'kato_core_lib.main.KatoInstance.init'
        ), patch(
            'kato_core_lib.main.KatoInstance.get', return_value=app,
        ), patch(
            'kato_core_lib.main._run_task_scan_loop'
        ):
            return main(self.cfg)

    def test_main_refuses_when_no_pin_and_no_optout(self) -> None:
        """Strict-by-default: no env vars → main() returns 1.

        Locks the production wiring. If a future refactor drops the
        ``validate_anthropic_tls_pin_or_refuse`` call from main, this
        test fails because main() returns 0 instead of 1.
        """
        # Both env vars are absent (setUp dropped them).
        result = self._run_main_with_other_validators_mocked()
        self.assertEqual(result, 1)

    def test_main_proceeds_when_optout_is_set(self) -> None:
        """``KATO_SANDBOX_ALLOW_NO_TLS_PIN=true`` opts out — main proceeds."""
        os.environ['KATO_SANDBOX_ALLOW_NO_TLS_PIN'] = 'true'
        try:
            result = self._run_main_with_other_validators_mocked()
        finally:
            del os.environ['KATO_SANDBOX_ALLOW_NO_TLS_PIN']
        self.assertEqual(result, 0)

    def test_main_invokes_tls_pin_validator(self) -> None:
        """Direct integration check: the validator function is called.

        Even if both opt-in and opt-out env vars were absent, the
        validator MUST be invoked — its absence would silently
        disable the OG4 protection. Patches the validator at the
        ``kato_core_lib.main`` module to verify the call site.
        """
        os.environ['KATO_SANDBOX_ALLOW_NO_TLS_PIN'] = 'true'
        try:
            with patch(
                'kato_core_lib.main.validate_anthropic_tls_pin_or_refuse',
            ) as mock_validator:
                self._run_main_with_other_validators_mocked()
        finally:
            del os.environ['KATO_SANDBOX_ALLOW_NO_TLS_PIN']
        mock_validator.assert_called_once()
