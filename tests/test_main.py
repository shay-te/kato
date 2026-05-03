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
from tests.utils import build_test_cfg


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

    The validator now implements a TOFU lifecycle (env var / opt-out
    / first-run / subsequent-run); the lifecycle's own behavior is
    tested in ``test_tls_pin.py``. This class only locks the
    ``main()`` ↔ validator wiring: that ``main()`` invokes the
    validator on every startup and propagates ``TlsPinError`` to a
    non-zero exit code.

    The opt-out path is the most convenient one to drive end-to-end
    here: it returns silently without touching the network or the
    filesystem, which keeps the test hermetic.
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
        # Drop the TLS env vars if a previous test or shell set them.
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

        Even when the validator's own decision is to return silently,
        the call MUST happen on every startup — its absence would
        silently disable the OG4 protection. Patches the validator
        at the ``kato_core_lib.main`` module to verify the call site.
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

    def test_main_returns_one_when_validator_raises(self) -> None:
        """Refusal path: a ``TlsPinError`` from the validator → exit 1.

        Locks the error-propagation half of the wiring. If a future
        refactor swallows the exception or returns 0 in the error
        path, this test fails. Uses the env-var ambiguity case (both
        env vars set → ``Pick one``) as the trigger because it's
        deterministic and doesn't need network or file mocking.
        """
        os.environ['KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256'] = (
            'QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE='  # 32 'A' bytes
        )
        os.environ['KATO_SANDBOX_ALLOW_NO_TLS_PIN'] = 'true'
        try:
            result = self._run_main_with_other_validators_mocked()
        finally:
            del os.environ['KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256']
            del os.environ['KATO_SANDBOX_ALLOW_NO_TLS_PIN']
        self.assertEqual(result, 1)


class MainReadOnlyToolsIntegrationTests(unittest.TestCase):
    """Locks the read-only-tools wiring: ``main()`` calls the gate.

    Without this test, ``validate_read_only_tools_requires_docker``
    is just a function in a module — a refactor that drops the call
    from ``main()`` would silently let
    ``KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS=true`` flow through to a
    host-mode spawn where pre-approved ``grep`` reads the operator's
    home directory.
    """

    def setUp(self) -> None:
        self.cfg = build_test_cfg()
        self._env_patch = patch.dict(
            'os.environ',
            {
                'KATO_IGNORED_REPOSITORY_FOLDERS': '',
                # Opt out of TLS pin so this class focuses on the
                # read-only gate, not the OG4 gate.
                'KATO_SANDBOX_ALLOW_NO_TLS_PIN': 'true',
            },
            clear=False,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        # Drop the read-only flag if a previous test or shell set it.
        for key in (
            'KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS',
            'KATO_CLAUDE_DOCKER',
        ):
            if key in os.environ:
                del os.environ[key]

    def _run_main_with_other_validators_mocked(self) -> int:
        app = types.SimpleNamespace(logger=Mock())
        with patch('kato_core_lib.main.validate_environment'), patch(
            'kato_core_lib.main.validate_bypass_permissions'
        ), patch(
            'kato_core_lib.main.print_security_posture'
        ), patch(
            'kato_core_lib.main.validate_anthropic_tls_pin_or_refuse'
        ), patch(
            'kato_core_lib.main.KatoInstance.init'
        ), patch(
            'kato_core_lib.main.KatoInstance.get', return_value=app,
        ), patch(
            'kato_core_lib.main._run_task_scan_loop'
        ):
            return main(self.cfg)

    def test_main_refuses_when_read_only_set_without_docker(self) -> None:
        """Strict gate: read-only=true alone -> main() returns 1."""
        os.environ['KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS'] = 'true'
        try:
            result = self._run_main_with_other_validators_mocked()
        finally:
            del os.environ['KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS']
        self.assertEqual(result, 1)

    def test_main_proceeds_when_both_set(self) -> None:
        """The valid combination: read-only=true + docker=true."""
        os.environ['KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS'] = 'true'
        os.environ['KATO_CLAUDE_DOCKER'] = 'true'
        try:
            # ``check_docker_or_exit`` would otherwise probe the
            # daemon; patch it (and the gVisor probe) for the same
            # reason the existing main tests do.
            with patch(
                'kato_core_lib.sandbox.manager.check_docker_or_exit'
            ), patch(
                'kato_core_lib.sandbox.manager.check_gvisor_or_exit'
            ), patch(
                'kato_core_lib.sandbox.manager.gvisor_runtime_available',
                return_value=False,
            ), patch(
                'kato_core_lib.sandbox.manager.docker_running_rootless',
                return_value=False,
            ):
                result = self._run_main_with_other_validators_mocked()
        finally:
            del os.environ['KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS']
            del os.environ['KATO_CLAUDE_DOCKER']
        self.assertEqual(result, 0)

    def test_main_invokes_read_only_validator(self) -> None:
        """Direct integration check: the validator function is called."""
        with patch(
            'kato_core_lib.main.validate_read_only_tools_requires_docker',
        ) as mock_validator:
            self._run_main_with_other_validators_mocked()
        mock_validator.assert_called_once()
