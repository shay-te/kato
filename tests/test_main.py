import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, call, patch


from kato_core_lib.main import (
    _RESUME_CONTINUE_PROMPT,
    _RESUME_WAIT_PROMPT,
    _cleanup_done_tasks_at_boot,
    _requeue_stuck_comments,
    _start_pending_comment_work_after_ui,
    _start_pending_comment_work,
    _start_pending_comment_work_when_ui_ready,
    _reset_stuck_workspace_statuses,
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
                'AGENT_IGNORED_REPOSITORY_FOLDERS': '',
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
        app = types.SimpleNamespace(logger=Mock(), needs_config=False)

        with patch(
            'kato_core_lib.main.collect_config_errors', return_value=[],
        ) as mock_collect_config_errors, patch(
            'kato_core_lib.main.KatoInstance.init'
        ) as mock_init, patch(
            'kato_core_lib.main.KatoInstance.get',
            return_value=app,
        ), patch(
            # The configured boot spawns this in a background thread; stub it
            # so the test doesn't leak a real validation-retry loop.
            'kato_core_lib.main._finalize_configured_boot',
        ), patch('kato_core_lib.main._run_task_scan_loop') as mock_run_loop:
            result = main(self.cfg)

        self.assertEqual(result, 0)
        mock_collect_config_errors.assert_called_once_with(mode='all')
        # UI-first boot: the service is built WITHOUT inline validation
        # (defer_validation=True); validation runs in the background finalize.
        mock_init.assert_called_once_with(
            self.cfg, setup_mode=False, defer_validation=True,
        )
        # The scan loop is gated on the background finalize via ready_event so
        # no scan runs until connections are validated + reconciliation is done.
        mock_run_loop.assert_called_once_with(
            app,
            scan_interval_seconds=60.0,
            force_scan_event=ANY,
            ready_event=ANY,
        )
        app.logger.info.assert_any_call('Starting kato agent')

    def test_main_configures_logger_when_app_logger_is_missing(self) -> None:
        configured_logger = Mock()
        app = types.SimpleNamespace(logger=None, needs_config=False)

        with patch(
            'kato_core_lib.main.collect_config_errors', return_value=[],
        ), patch(
            'kato_core_lib.main.configure_logger', return_value=configured_logger
        ), patch(
            'kato_core_lib.main.KatoInstance.init'
        ), patch(
            'kato_core_lib.main.KatoInstance.get',
            return_value=app,
        ), patch(
            'kato_core_lib.main._finalize_configured_boot',
        ), patch('kato_core_lib.main._run_task_scan_loop'):
            main(self.cfg)

        self.assertIs(app.logger, configured_logger)

    def test_run_task_scan_loop_scans_immediately_and_sleeps_between_cycles(self) -> None:
        # The vestigial OpenHands warm-up delay is gone: the first scan fires
        # right away (the UI is already served), and the only sleeps are the
        # heartbeat chunks between ticks.
        app = types.SimpleNamespace(logger=Mock())
        job = Mock()
        job.run.side_effect = [None, None]

        with patch('kato_core_lib.main.ProcessAssignedTasksJob', return_value=job) as mock_job_cls, patch(
            'kato_core_lib.main.supports_inline_status',
            return_value=False,
        ), patch('kato_core_lib.main.time.sleep') as mock_sleep:
            _run_task_scan_loop(
                app,
                scan_interval_seconds=60.0,
                sleep_fn=mock_sleep,
                max_cycles=2,
            )

        mock_job_cls.assert_called_once_with()
        job.initialized.assert_called_once_with(app)
        self.assertEqual(job.run.call_count, 2)
        # No startup sleep — all sleeps are the between-tick heartbeat chunks
        # (the 60s interval split into 5s chunks) for the single inter-cycle gap.
        sleep_durations = [call_obj.args[0] for call_obj in mock_sleep.call_args_list]
        self.assertAlmostEqual(sum(sleep_durations), 60.0, delta=5.0)
        # The old "while Kato warms up" line must be gone.
        for call_obj in app.logger.info.call_args_list:
            self.assertNotIn('warms up', str(call_obj))

    def test_run_task_scan_loop_continues_after_failure(self) -> None:
        app = types.SimpleNamespace(logger=Mock())
        job = Mock()
        job.run.side_effect = [RuntimeError('service down'), None]

        with patch('kato_core_lib.main.ProcessAssignedTasksJob', return_value=job), patch(
            'kato_core_lib.main.time.sleep'
        ) as mock_sleep:
            _run_task_scan_loop(
                app,
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
            {'AGENT_IGNORED_REPOSITORY_FOLDERS': 'secret-client'},
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

    def test_resume_streaming_sessions_recovers_latest_agent_session_id_after_restart(self) -> None:
        """End-to-end: a kato restart re-attaches the existing chat to its
        most recently persisted Claude session id, not a fresh session.

        Sets up a real ``ClaudeSessionManager`` (not a mock) pointed at a
        temp state dir. Starts a session for PROJ-1 — this writes
        ``agent_session_id`` to ``<state_dir>/PROJ-1.json``. Then
        simulates "kato restart" by building a brand-new manager pointed
        at the same dir and feeding it through ``_resume_streaming_sessions``.
        Asserts that the resumed session inherits the persisted session id,
        which is what makes the chat resume from where it left off instead
        of starting a fresh conversation.
        """
        from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
        from claude_core_lib.claude_core_lib.tests.session.test_manager import _FakeStreamingSession

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            first_fakes: list = []

            def first_factory(**kwargs):
                s = _FakeStreamingSession(**kwargs)
                first_fakes.append(s)
                return s

            # --- Run 1: start a session, capture the persisted agent_session_id
            first_manager = ClaudeSessionManager(
                state_dir=state_dir, session_factory=first_factory,
            )
            first_manager.start_session(task_id='PROJ-1', task_summary='resume me')
            persisted_session_id = first_fakes[0].agent_session_id
            self.assertTrue(persisted_session_id)

            # --- Simulated restart: new manager, same state_dir, no in-memory carry-over
            second_fakes: list = []

            def second_factory(**kwargs):
                s = _FakeStreamingSession(**kwargs)
                second_fakes.append(s)
                return s

            rebooted_manager = ClaudeSessionManager(
                state_dir=state_dir, session_factory=second_factory,
            )

            workspace_root = types.SimpleNamespace(is_dir=Mock(return_value=True))
            workspace_manager = types.SimpleNamespace(
                list_workspaces=Mock(
                    return_value=[
                        types.SimpleNamespace(
                            task_id='PROJ-1',
                            task_summary='resume me',
                            status='active',
                            cwd='',
                            repository_ids=['client'],
                        )
                    ]
                ),
                repository_path=Mock(return_value=workspace_root),
                update_agent_session=Mock(),
            )
            app = types.SimpleNamespace(
                logger=Mock(),
                session_manager=rebooted_manager,
                workspace_manager=workspace_manager,
                planning_session_runner=None,
            )

            _resume_streaming_sessions(app)

            # Exactly one new session spawned, and it inherits the
            # persisted agent_session_id as its resume target — proving
            # the chat picks up where the previous kato run left off.
            self.assertEqual(len(second_fakes), 1)
            self.assertEqual(second_fakes[0].resume_session_id, persisted_session_id)

    def test_resume_streaming_sessions_keeps_original_id_when_re_adopt_is_attempted(self) -> None:
        """Restart resume must use the first pinned session id."""
        from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
        from claude_core_lib.claude_core_lib.tests.session.test_manager import _FakeStreamingSession

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)

            # Manager 1: first session
            fakes_1: list = []
            mgr_1 = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=lambda **kw: fakes_1.append(_FakeStreamingSession(**kw)) or fakes_1[-1],
            )
            mgr_1.start_session(task_id='PROJ-1', task_summary='first run')
            persisted_session_id = mgr_1.get_record('PROJ-1').agent_session_id
            mgr_1.terminate_session('PROJ-1')

            # Manager 2 (simulated restart 1): a different adoption is refused.
            fakes_2: list = []
            mgr_2 = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=lambda **kw: fakes_2.append(_FakeStreamingSession(**kw)) or fakes_2[-1],
            )
            with self.assertRaises(RuntimeError):
                mgr_2.adopt_session_id('PROJ-1', agent_session_id='newer-session-uuid')
            latest_session_id = mgr_2.get_record('PROJ-1').agent_session_id
            self.assertEqual(latest_session_id, persisted_session_id)

            # Manager 3 (simulated restart 2): _resume_streaming_sessions
            # MUST pick up the original pinned id, not the rejected id.
            fakes_3: list = []
            mgr_3 = ClaudeSessionManager(
                state_dir=state_dir,
                session_factory=lambda **kw: fakes_3.append(_FakeStreamingSession(**kw)) or fakes_3[-1],
            )
            workspace_manager = types.SimpleNamespace(
                list_workspaces=Mock(
                    return_value=[
                        types.SimpleNamespace(
                            task_id='PROJ-1',
                            task_summary='first run',
                            status='active',
                            cwd='/repo',
                            repository_ids=['client'],
                        )
                    ]
                ),
                update_agent_session=Mock(),
            )
            app = types.SimpleNamespace(
                logger=Mock(),
                session_manager=mgr_3,
                workspace_manager=workspace_manager,
                planning_session_runner=None,
            )

            _resume_streaming_sessions(app)

            self.assertEqual(len(fakes_3), 1)
            self.assertEqual(fakes_3[0].resume_session_id, persisted_session_id)

    def test_resume_streaming_sessions_seeds_from_workspace_metadata_before_spawn(self) -> None:
        """Empty manager state still resumes the id stored on workspace metadata."""
        from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
        from claude_core_lib.claude_core_lib.tests.session.test_manager import _FakeStreamingSession

        with tempfile.TemporaryDirectory() as tmp:
            fakes: list = []

            def factory(**kwargs):
                session = _FakeStreamingSession(**kwargs)
                fakes.append(session)
                return session

            manager = ClaudeSessionManager(
                state_dir=Path(tmp),
                session_factory=factory,
            )
            workspace_record = types.SimpleNamespace(
                task_id='PROJ-1',
                task_summary='from workspace',
                status='active',
                cwd='/repo',
                repository_ids=['client'],
                agent_session_id='workspace-session-id',
            )
            workspace_manager = types.SimpleNamespace(
                list_workspaces=Mock(return_value=[workspace_record]),
                update_agent_session=Mock(),
            )
            app = types.SimpleNamespace(
                logger=Mock(),
                session_manager=manager,
                workspace_manager=workspace_manager,
                planning_session_runner=None,
            )

            _resume_streaming_sessions(app)

            self.assertEqual(len(fakes), 1)
            self.assertEqual(fakes[0].resume_session_id, 'workspace-session-id')
            self.assertEqual(
                manager.get_record('PROJ-1').agent_session_id,
                'workspace-session-id',
            )

    def _run_setup_mode_main(self, wait_result, app):
        """Drive ``main()`` through the setup-mode branch with the wait loop
        stubbed to either shut down (``False``) or finish setup (``True``)."""
        with patch(
            'kato_core_lib.main.configure_logger', return_value=app.logger,
        ), patch(
            'kato_core_lib.main.collect_config_errors',
            return_value=['missing required agent env var: YOUTRACK_TOKEN'],
        ) as mock_collect, patch(
            'kato_core_lib.main.KatoInstance.init',
        ) as mock_init, patch(
            'kato_core_lib.main.KatoInstance.get', return_value=app,
        ), patch(
            'kato_core_lib.main._start_planning_webserver_if_enabled',
        ) as mock_webserver, patch(
            'kato_core_lib.main._register_shutdown_hook',
        ) as mock_shutdown_hook, patch(
            'kato_core_lib.main._wait_until_configured_then_finish_setup',
            return_value=wait_result,
        ) as mock_wait, patch(
            'kato_core_lib.main._run_task_scan_loop',
        ) as mock_run_loop:
            result = main(self.cfg)
        return result, {
            'collect': mock_collect,
            'init': mock_init,
            'webserver': mock_webserver,
            'shutdown_hook': mock_shutdown_hook,
            'wait': mock_wait,
            'run_loop': mock_run_loop,
        }

    def test_main_boots_setup_mode_when_config_is_incomplete(self) -> None:
        """Missing config no longer hard-exits — kato boots the setup UI.

        The previous behavior returned 1 and never called
        ``KatoInstance.init``. Now an unconfigured operator gets a running
        webserver so they can fill in Settings from the browser (no terminal
        work). ``KatoInstance.init`` is called with ``setup_mode=True``, the
        scan loop stays OFF (there is no ticket service yet), and ``main()``
        parks on the configuration wait loop. A ``False`` from the wait loop
        (shutdown signal) exits 0 cleanly.
        """
        configured_logger = Mock()
        app = types.SimpleNamespace(logger=configured_logger, needs_config=True)

        result, mocks = self._run_setup_mode_main(False, app)

        self.assertEqual(result, 0)
        mocks['collect'].assert_called_once_with(mode='all')
        mocks['init'].assert_called_once_with(
            self.cfg, setup_mode=True, defer_validation=True,
        )
        # The operator gets a running UI to configure from...
        mocks['webserver'].assert_called_once_with(app)
        mocks['shutdown_hook'].assert_called_once_with(app)
        mocks['wait'].assert_called_once_with(app)
        # ...but the scan loop MUST stay off — kato can't scan tickets
        # until it's configured.
        mocks['run_loop'].assert_not_called()
        # The terminal gets ONE concise line — the wizard shows the details.
        configured_logger.warning.assert_any_call(
            'kato is not configured yet (%d setting(s) missing) — opening '
            'the setup wizard in the browser.', 1,
        )

    def test_main_continues_into_full_boot_after_setup_completes(self) -> None:
        """Terminal-free apply: when the wait loop reports setup finished
        (operator completed configuration in the UI and ``complete_setup``
        built the agent service), ``main()`` falls through into the normal
        boot — the scan loop starts in the SAME process, no restart."""
        app = types.SimpleNamespace(logger=Mock(), needs_config=True)

        with patch(
            'kato_core_lib.main._mark_webserver_configured',
        ) as mock_mark:
            result, mocks = self._run_setup_mode_main(True, app)

        self.assertEqual(result, 0)
        mocks['wait'].assert_called_once_with(app)
        # The full boot ran: the UI left setup mode and the scan loop is live.
        mock_mark.assert_called_once_with(app)
        mocks['run_loop'].assert_called_once()

    def test_ui_leaves_setup_mode_only_after_boot_reconciliation(self) -> None:
        # Source-order guard: on the setup fall-through, the gate must stay up
        # while the boot reconciliation runs — flipping earlier lets the
        # operator race branch checkouts / comment requeue with chat sends the
        # moment the gate closes. The reconciliation steps now live in
        # ``_run_boot_reconciliation``; the setup branch calls it before
        # ``_mark_webserver_configured`` and before the scan loop.
        import inspect
        from kato_core_lib import main as main_module
        src = inspect.getsource(main_module.main)
        mark_idx = src.index('_mark_webserver_configured(app)')
        self.assertLess(src.index('_run_boot_reconciliation(app)'), mark_idx)
        self.assertLess(mark_idx, src.index('_run_task_scan_loop('))
        # And the reconciliation helper actually runs the recover/requeue steps.
        recon = inspect.getsource(main_module._run_boot_reconciliation)
        self.assertIn('_recover_orphan_workspaces(app)', recon)
        self.assertIn('_requeue_stuck_comments(app)', recon)

    def test_docker_mode_on_runs_sandbox_preflight(self) -> None:
        """``KATO_CLAUDE_DOCKER=true`` must run the sandbox daemon checks.

        Locks the Phase 2 gate at ``main.py:86``. If a future refactor
        reverts ``is_docker_mode_enabled()`` back to ``is_bypass_enabled()``,
        ``docker=true, bypass=false`` operators silently lose the docker
        daemon preflight — exactly the case this gate exists to catch.
        """
        app = types.SimpleNamespace(logger=Mock())

        with patch(
            'kato_core_lib.main.collect_config_errors', return_value=[],
        ), patch(
            'kato_core_lib.main.validate_bypass_permissions'
        ), patch(
            'kato_core_lib.main.print_security_posture'
        ), patch(
            'kato_core_lib.main.KatoInstance.init'
        ), patch(
            'kato_core_lib.main.KatoInstance.get', return_value=app,
        ), patch(
            'kato_core_lib.main._finalize_configured_boot'
        ), patch(
            'kato_core_lib.main._run_task_scan_loop'
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator.is_docker_mode_enabled',
            return_value=True,
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.manager.check_docker_or_exit'
        ) as mock_check_docker, patch(
            'sandbox_core_lib.sandbox_core_lib.manager.check_gvisor_or_exit'
        ) as mock_check_gvisor, patch(
            'sandbox_core_lib.sandbox_core_lib.manager.gvisor_runtime_available',
            return_value=True,
        ) as mock_gvisor_runtime, patch(
            'sandbox_core_lib.sandbox_core_lib.manager.docker_running_rootless',
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

        with patch(
            'kato_core_lib.main.collect_config_errors', return_value=[],
        ), patch(
            'kato_core_lib.main.validate_bypass_permissions'
        ), patch(
            'kato_core_lib.main.print_security_posture'
        ), patch(
            'kato_core_lib.main.KatoInstance.init'
        ), patch(
            'kato_core_lib.main.KatoInstance.get', return_value=app,
        ), patch(
            'kato_core_lib.main._finalize_configured_boot'
        ), patch(
            'kato_core_lib.main._run_task_scan_loop'
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator.is_docker_mode_enabled',
            return_value=False,
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.manager.check_docker_or_exit'
        ) as mock_check_docker, patch(
            'sandbox_core_lib.sandbox_core_lib.manager.check_gvisor_or_exit'
        ) as mock_check_gvisor, patch(
            'sandbox_core_lib.sandbox_core_lib.manager.gvisor_runtime_available'
        ) as mock_gvisor_runtime, patch(
            'sandbox_core_lib.sandbox_core_lib.manager.docker_running_rootless'
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
            {'AGENT_IGNORED_REPOSITORY_FOLDERS': ''},
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
        with patch(
            'kato_core_lib.main.collect_config_errors', return_value=[],
        ), patch(
            'kato_core_lib.main.validate_bypass_permissions'
        ), patch(
            'kato_core_lib.main.print_security_posture'
        ), patch(
            'kato_core_lib.main.KatoInstance.init'
        ), patch(
            'kato_core_lib.main.KatoInstance.get', return_value=app,
        ), patch(
            'kato_core_lib.main._finalize_configured_boot'
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
                'AGENT_IGNORED_REPOSITORY_FOLDERS': '',
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
        with patch(
            'kato_core_lib.main.collect_config_errors', return_value=[],
        ), patch(
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
            'kato_core_lib.main._finalize_configured_boot'
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
                'sandbox_core_lib.sandbox_core_lib.manager.check_docker_or_exit'
            ), patch(
                'sandbox_core_lib.sandbox_core_lib.manager.check_gvisor_or_exit'
            ), patch(
                'sandbox_core_lib.sandbox_core_lib.manager.gvisor_runtime_available',
                return_value=False,
            ), patch(
                'sandbox_core_lib.sandbox_core_lib.manager.docker_running_rootless',
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


class CleanupDoneTasksAtBootTests(unittest.TestCase):
    """Boot-time prune so a restart never resurrects a done task's tab.

    The "task is back after restart" bug: cleanup only ran on a scan
    tick, so a stale ``~/.kato/sessions/<id>.json`` left a tab on
    screen until the first tick ~30s later. This runs the prune at
    boot, before the webserver serves the tab list.
    """

    def test_delegates_to_agent_service_cleanup(self) -> None:
        cleanup = Mock()
        app = types.SimpleNamespace(
            logger=Mock(),
            service=types.SimpleNamespace(cleanup_done_tasks=cleanup),
        )
        _cleanup_done_tasks_at_boot(app)
        cleanup.assert_called_once_with()

    def test_noop_when_service_missing(self) -> None:
        app = types.SimpleNamespace(logger=Mock())
        _cleanup_done_tasks_at_boot(app)  # no raise

    def test_noop_when_service_lacks_method(self) -> None:
        app = types.SimpleNamespace(
            logger=Mock(), service=types.SimpleNamespace(),
        )
        _cleanup_done_tasks_at_boot(app)  # no raise

    def test_swallows_cleanup_exception(self) -> None:
        cleanup = Mock(side_effect=RuntimeError('platform down'))
        app = types.SimpleNamespace(
            logger=Mock(),
            service=types.SimpleNamespace(cleanup_done_tasks=cleanup),
        )
        _cleanup_done_tasks_at_boot(app)  # must NOT raise — boot continues
        app.logger.exception.assert_called()

    def test_runs_in_boot_reconciliation_before_scan_release(self) -> None:
        # UI-FIRST boot contract (intentional change): the configured boot
        # now serves the webserver FIRST and runs the done-task prune in the
        # background finalize — so a restart may briefly show a done tab for a
        # few seconds until reconciliation completes. The prune is part of
        # ``_run_boot_reconciliation``, which the finalize runs before
        # ``_mark_webserver_configured`` and before it releases the scan loop.
        import inspect
        from kato_core_lib import main as kato_main
        recon = inspect.getsource(kato_main._run_boot_reconciliation)
        self.assertIn('_cleanup_done_tasks_at_boot(app)', recon)
        finalize = inspect.getsource(kato_main._finalize_configured_boot)
        self.assertLess(
            finalize.index('_run_boot_reconciliation(app)'),
            finalize.index('ready_event.set()'),
        )
        self.assertLess(
            finalize.index('_run_boot_reconciliation(app)'),
            finalize.index('_mark_webserver_configured(app)'),
        )


class ResetStuckWorkspaceStatusesTests(unittest.TestCase):
    """Tests for _reset_stuck_workspace_statuses (Fix 3 boot-time status repair)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace_root = Path(self._tmp.name)
        from workspace_core_lib.workspace_core_lib import WorkspaceCoreLib
        self._lib = WorkspaceCoreLib(
            root=self.workspace_root,
            max_parallel_tasks=2,
            metadata_filename='.kato-meta.json',
            preflight_log_filename='.kato-preflight.log',
        )
        self.workspace_manager = self._lib.workspaces

    def _make_app(self):
        return types.SimpleNamespace(
            logger=Mock(),
            workspace_manager=self.workspace_manager,
        )

    def _create_workspace(self, task_id, status, repo_ids=None):
        from workspace_core_lib.workspace_core_lib import (
            WORKSPACE_STATUS_PROVISIONING,
        )
        record = self.workspace_manager.create(
            task_id=task_id,
            task_summary='test task',
            repository_ids=repo_ids or [],
        )
        self.workspace_manager.update_status(task_id, status)
        return record

    def _add_git_repo(self, task_id, repo_id):
        repo_path = self.workspace_root / task_id / repo_id
        repo_path.mkdir(parents=True, exist_ok=True)
        (repo_path / '.git').mkdir(exist_ok=True)

    def test_provisioning_with_git_repo_is_promoted_to_active(self) -> None:
        self._create_workspace('PROJ-1', 'provisioning', ['client'])
        self._add_git_repo('PROJ-1', 'client')
        app = self._make_app()

        _reset_stuck_workspace_statuses(app)

        record = self.workspace_manager.get('PROJ-1')
        self.assertEqual(record.status, 'active')
        app.logger.info.assert_any_call(
            'workspace %s promoted from provisioning to active '
            '(repos were cloned before the previous kato process stopped)',
            'PROJ-1',
        )

    def test_provisioning_without_git_repo_is_not_promoted(self) -> None:
        self._create_workspace('PROJ-2', 'provisioning', ['client'])
        app = self._make_app()

        _reset_stuck_workspace_statuses(app)

        record = self.workspace_manager.get('PROJ-2')
        self.assertEqual(record.status, 'provisioning')
        app.logger.warning.assert_any_call(
            'workspace %s is stuck in provisioning state with no valid '
            'git repos — the previous clone was incomplete. '
            'Re-run the task to provision it correctly.',
            'PROJ-2',
        )

    def test_errored_workspace_logs_warning_without_status_change(self) -> None:
        self._create_workspace('PROJ-3', 'errored', ['client'])
        app = self._make_app()

        _reset_stuck_workspace_statuses(app)

        record = self.workspace_manager.get('PROJ-3')
        self.assertEqual(record.status, 'errored')
        app.logger.warning.assert_any_call(
            'workspace %s is in errored state from a previous run — '
            'operator may need to re-run the task or discard the workspace',
            'PROJ-3',
        )

    def test_active_workspace_is_left_unchanged(self) -> None:
        self._create_workspace('PROJ-4', 'active', ['client'])
        app = self._make_app()

        _reset_stuck_workspace_statuses(app)

        record = self.workspace_manager.get('PROJ-4')
        self.assertEqual(record.status, 'active')
        app.logger.info.assert_not_called()

    def test_review_workspace_is_left_unchanged(self) -> None:
        self._create_workspace('PROJ-5', 'review', ['client'])
        app = self._make_app()

        _reset_stuck_workspace_statuses(app)

        record = self.workspace_manager.get('PROJ-5')
        self.assertEqual(record.status, 'review')

    def test_noop_when_workspace_manager_is_none(self) -> None:
        app = types.SimpleNamespace(
            logger=Mock(),
            workspace_manager=None,
        )
        _reset_stuck_workspace_statuses(app)
        app.logger.info.assert_not_called()
        app.logger.warning.assert_not_called()

    def test_noop_when_workspace_manager_attribute_missing(self) -> None:
        app = types.SimpleNamespace(logger=Mock())
        _reset_stuck_workspace_statuses(app)
        app.logger.info.assert_not_called()

    def test_promotion_count_logged_when_multiple_workspaces_promoted(self) -> None:
        for i in (1, 2):
            self._create_workspace(f'PROJ-{i}', 'provisioning', ['client'])
            self._add_git_repo(f'PROJ-{i}', 'client')
        app = self._make_app()

        _reset_stuck_workspace_statuses(app)

        app.logger.info.assert_any_call(
            'promoted %d workspace(s) from provisioning to active at boot',
            2,
        )


class RequeueStuckCommentsBootTests(unittest.TestCase):
    """_requeue_stuck_comments delegates to the service and logs a count."""

    def test_delegates_and_logs_when_comments_requeued(self) -> None:
        service = types.SimpleNamespace(
            requeue_stuck_in_progress_comments=Mock(return_value=[
                {'task_id': 'UNA-1', 'comment_id': 'c1'},
                {'task_id': 'UNA-2', 'comment_id': 'c2'},
            ]),
        )
        app = types.SimpleNamespace(logger=Mock(), service=service)

        _requeue_stuck_comments(app)

        service.requeue_stuck_in_progress_comments.assert_called_once_with()
        app.logger.info.assert_called_once_with(
            'requeued %d comment(s) stuck in-progress from the previous '
            'run; _start_pending_comment_work will dispatch them next',
            2,
        )

    def test_silent_when_nothing_requeued(self) -> None:
        service = types.SimpleNamespace(
            requeue_stuck_in_progress_comments=Mock(return_value=[]),
        )
        app = types.SimpleNamespace(logger=Mock(), service=service)

        _requeue_stuck_comments(app)

        app.logger.info.assert_not_called()

    def test_service_error_does_not_abort_boot(self) -> None:
        service = types.SimpleNamespace(
            requeue_stuck_in_progress_comments=Mock(
                side_effect=RuntimeError('boom'),
            ),
        )
        app = types.SimpleNamespace(logger=Mock(), service=service)

        _requeue_stuck_comments(app)  # must not raise

        app.logger.exception.assert_called_once()

    def test_noop_when_service_missing_or_method_absent(self) -> None:
        _requeue_stuck_comments(types.SimpleNamespace(logger=Mock()))
        app = types.SimpleNamespace(logger=Mock(), service=object())
        _requeue_stuck_comments(app)
        app.logger.info.assert_not_called()

    def test_boot_order_requeue_runs_before_scan_loop(self) -> None:
        import inspect
        from kato_core_lib import main as main_module
        # The reconciliation ordering now lives in ``_run_boot_reconciliation``:
        # requeue runs after the workspace status reset (so workspaces are
        # ACTIVE first).
        recon = inspect.getsource(main_module._run_boot_reconciliation)
        self.assertLess(
            recon.index('_reset_stuck_workspace_statuses(app)'),
            recon.index('_requeue_stuck_comments(app)'),
        )
        # And the configured finalize runs the whole reconciliation before it
        # releases the scan loop (ready_event), so nothing scans a stale queue.
        finalize = inspect.getsource(main_module._finalize_configured_boot)
        self.assertLess(
            finalize.index('_run_boot_reconciliation(app)'),
            finalize.index('ready_event.set()'),
        )


class StartPendingCommentWorkBootTests(unittest.TestCase):
    """_start_pending_comment_work dispatches queued comments at boot
    so the agent starts immediately, not on the first scan tick."""

    def test_delegates_and_logs_started_count(self) -> None:
        service = types.SimpleNamespace(
            drain_all_queued_task_comments=Mock(return_value=[
                {'task_id': 'UNA-1', 'started': True, 'comment_id': 'c1'},
                {'task_id': 'UNA-2', 'started': True, 'comment_id': 'c2'},
            ]),
        )
        app = types.SimpleNamespace(logger=Mock(), service=service)

        _start_pending_comment_work(app)

        service.drain_all_queued_task_comments.assert_called_once_with()
        app.logger.info.assert_called_once_with(
            'started agent work on %d task(s) with queued comments at boot',
            2,
        )

    def test_silent_when_nothing_queued(self) -> None:
        service = types.SimpleNamespace(
            drain_all_queued_task_comments=Mock(return_value=[]),
        )
        app = types.SimpleNamespace(logger=Mock(), service=service)

        _start_pending_comment_work(app)

        app.logger.info.assert_not_called()

    def test_service_error_does_not_abort_boot(self) -> None:
        service = types.SimpleNamespace(
            drain_all_queued_task_comments=Mock(
                side_effect=RuntimeError('boom'),
            ),
        )
        app = types.SimpleNamespace(logger=Mock(), service=service)

        _start_pending_comment_work(app)  # must not raise

        app.logger.exception.assert_called_once()

    def test_noop_when_service_missing_or_method_absent(self) -> None:
        _start_pending_comment_work(types.SimpleNamespace(logger=Mock()))
        app = types.SimpleNamespace(logger=Mock(), service=object())
        _start_pending_comment_work(app)
        app.logger.info.assert_not_called()

    def test_boot_order_dispatch_is_deferred_until_after_webserver_start(self) -> None:
        import inspect
        from kato_core_lib import main as main_module
        # UI-first boot: the webserver starts in main() BEFORE the background
        # finalize thread that runs reconciliation + the post-boot workers, so
        # comment resume work can never delay the planning page.
        src = inspect.getsource(main_module.main)
        # rindex → the fast-path serve (the setup branch has earlier ones);
        # match the actual spawn call, not the comment that references it.
        self.assertLess(
            src.rindex('_start_planning_webserver_if_enabled(app)'),
            src.index('_finalize_configured_boot(app'),
        )
        # Inside the finalize, the stale IN_PROGRESS → QUEUED requeue (part of
        # reconciliation) runs before the deferred dispatch (part of the
        # post-boot workers), which runs before the scan loop is released.
        finalize = inspect.getsource(main_module._finalize_configured_boot)
        self.assertLess(
            finalize.index('_run_boot_reconciliation(app)'),
            finalize.index('_start_post_boot_workers(app)'),
        )
        self.assertLess(
            finalize.index('_start_post_boot_workers(app)'),
            finalize.index('ready_event.set()'),
        )
        workers = inspect.getsource(main_module._start_post_boot_workers)
        self.assertIn('_start_pending_comment_work_after_ui(app)', workers)
        recon = inspect.getsource(main_module._run_boot_reconciliation)
        self.assertIn('_requeue_stuck_comments(app)', recon)

    def test_deferred_dispatch_runs_in_background_thread(self) -> None:
        app = types.SimpleNamespace(logger=Mock())
        with patch('kato_core_lib.main.threading.Thread') as thread_cls:
            _start_pending_comment_work_after_ui(app)
        thread_cls.assert_called_once()
        self.assertEqual(
            thread_cls.call_args.kwargs['name'],
            'kato-start-pending-comments',
        )
        self.assertTrue(thread_cls.call_args.kwargs['daemon'])
        thread_cls.return_value.start.assert_called_once_with()

    def test_deferred_worker_waits_for_ui_healthz_before_dispatch(self) -> None:
        app = types.SimpleNamespace(
            logger=Mock(),
            planning_webserver_url='http://127.0.0.1:5050',
        )
        with patch(
            'kato_core_lib.main._wait_for_planning_ui_healthz',
        ) as wait, patch(
            'kato_core_lib.main._start_pending_comment_work',
        ) as start:
            _start_pending_comment_work_when_ui_ready(app)
        wait.assert_called_once_with(
            'http://127.0.0.1:5050', logger=app.logger,
        )
        start.assert_called_once_with(app)

    def test_deferred_worker_dispatches_immediately_without_ui_url(self) -> None:
        app = types.SimpleNamespace(logger=Mock())
        with patch(
            'kato_core_lib.main._wait_for_planning_ui_healthz',
        ) as wait, patch(
            'kato_core_lib.main._start_pending_comment_work',
        ) as start:
            _start_pending_comment_work_when_ui_ready(app)
        wait.assert_not_called()
        start.assert_called_once_with(app)


class WaitUntilConfiguredTests(unittest.TestCase):
    """The SETUP-MODE wait loop — the terminal-free apply path.

    Real config evaluation end-to-end: the loop reads an actual
    ``settings.json`` (redirected to a tmpfile via ``KATO_SETTINGS_FILE``)
    through the same ``effective_config_env`` + ``collect_config_errors``
    pair the ``/api/config-status`` endpoint uses. Only the app object and
    its ``complete_setup`` are test doubles — the config plumbing is real.
    """

    _REQUIRED_KEYS = (
        'YOUTRACK_API_BASE_URL', 'YOUTRACK_API_TOKEN', 'YOUTRACK_PROJECT',
        'YOUTRACK_ASSIGNEE', 'REPOSITORY_ROOT_PATH', 'OPENHANDS_BASE_URL',
        'OPENHANDS_API_KEY', 'OH_SECRET_KEY', 'OPENHANDS_LLM_MODEL',
        'OPENHANDS_LLM_API_KEY', 'KATO_ISSUE_PLATFORM', 'KATO_AGENT_BACKEND',
    )

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp_dir = Path(self._tmp.name)
        self.settings_path = tmp_dir / 'settings.json'
        self.projects = tmp_dir / 'projects'
        self.projects.mkdir()
        # patch.dict snapshots os.environ and restores it wholesale on stop,
        # so the pops below (shell-config isolation) and any keys the loop
        # loads during a test are all undone automatically.
        self._env_patch = patch.dict('os.environ', {
            'KATO_SETTINGS_FILE': str(self.settings_path),
            # The transition re-runs the boot security gates; opt out of the
            # TLS pin exactly like the other main() tests do.
            'KATO_SANDBOX_ALLOW_NO_TLS_PIN': 'true',
        }, clear=False)
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        for key in self._REQUIRED_KEYS + (
            # Gate-relevant flags a developer shell might carry.
            'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256',
            'KATO_CLAUDE_BYPASS_PERMISSIONS',
            'KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS',
            'KATO_CLAUDE_DOCKER',
        ):
            os.environ.pop(key, None)

    def _write_settings(self, extra=None) -> None:
        import json
        values = {
            'YOUTRACK_API_BASE_URL': 'https://youtrack.example',
            'YOUTRACK_API_TOKEN': 'yt-token',
            'YOUTRACK_PROJECT': 'PROJ',
            'YOUTRACK_ASSIGNEE': 'me',
            'REPOSITORY_ROOT_PATH': str(self.projects),
            'OPENHANDS_BASE_URL': 'https://openhands.example',
            'OPENHANDS_API_KEY': 'oh-key',
            'OH_SECRET_KEY': 'oh-secret',
            'OPENHANDS_LLM_MODEL': 'gpt-4',
            'OPENHANDS_LLM_API_KEY': 'llm-key',
        }
        values.update(extra or {})
        self.settings_path.write_text(json.dumps(values), encoding='utf-8')

    def _app(self, complete_setup):
        flask_app = types.SimpleNamespace(
            config={'AGENT_SERVICE': None, 'NEEDS_CONFIG': True},
        )
        return types.SimpleNamespace(
            logger=Mock(),
            complete_setup=complete_setup,
            planning_flask_app=flask_app,
        )

    def test_shutdown_signal_returns_false_without_attempting_setup(self) -> None:
        from kato_core_lib.main import _wait_until_configured_then_finish_setup
        complete_setup = Mock()
        app = self._app(complete_setup)

        def interrupted_sleep(_seconds):
            raise KeyboardInterrupt

        result = _wait_until_configured_then_finish_setup(
            app, sleep_fn=interrupted_sleep,
        )

        self.assertFalse(result)
        complete_setup.assert_not_called()

    def test_keeps_waiting_while_config_incomplete(self) -> None:
        from kato_core_lib.main import _wait_until_configured_then_finish_setup
        complete_setup = Mock()
        app = self._app(complete_setup)

        # Empty settings.json → still unconfigured every tick.
        result = _wait_until_configured_then_finish_setup(
            app, sleep_fn=lambda _s: None, max_ticks=3,
        )

        self.assertFalse(result)
        complete_setup.assert_not_called()
        # The webserver stays in setup mode.
        self.assertTrue(app.planning_flask_app.config['NEEDS_CONFIG'])

    def test_finishes_setup_and_clears_the_error_once_configured(self) -> None:
        from kato_core_lib.main import _wait_until_configured_then_finish_setup
        self._write_settings()
        service = object()
        app = self._app(None)
        # The real complete_setup assigns app.service; mirror that.
        app.complete_setup = Mock(side_effect=lambda: setattr(app, 'service', service))

        result = _wait_until_configured_then_finish_setup(
            app, sleep_fn=lambda _s: None, max_ticks=3,
        )

        self.assertTrue(result)
        app.complete_setup.assert_called_once_with()
        # The saved settings were loaded into the process env (so hydra's
        # ${oc.env:...} reads resolve to what the operator saved).
        self.assertEqual(os.environ.get('YOUTRACK_API_TOKEN'), 'yt-token')
        # Any earlier failure is cleared right away...
        self.assertEqual(app.planning_flask_app.config['SETUP_ERROR'], '')
        # ...but the UI is NOT flipped out of setup mode here: main() first
        # runs the boot reconciliation steps (orphan recovery, branch
        # reconcile, comment requeue) and only then calls
        # _mark_webserver_configured — otherwise the operator could race
        # those steps with chat sends the moment the gate closes.
        self.assertTrue(app.planning_flask_app.config['NEEDS_CONFIG'])

    def test_failed_attempt_rolls_back_env_and_retries_only_after_a_change(self) -> None:
        """Bad creds must not wedge setup mode: the failed attempt's env
        load is rolled back, the SAME config is not retried (no hammering
        the provider), and a corrected save goes through."""
        from kato_core_lib.main import _wait_until_configured_then_finish_setup
        self._write_settings({'YOUTRACK_API_TOKEN': 'bad-token'})
        app = self._app(Mock(side_effect=[RuntimeError('bad creds'), None]))
        ticks = {'n': 0}

        def scripted_sleep(_seconds):
            ticks['n'] += 1
            if ticks['n'] == 3:
                # The operator fixes the token in the Settings UI.
                self._write_settings({'YOUTRACK_API_TOKEN': 'good-token'})

        result = _wait_until_configured_then_finish_setup(
            app, sleep_fn=scripted_sleep, max_ticks=6,
        )

        self.assertTrue(result)
        # Attempt 1 (bad) + attempt 2 (good) — the unchanged-config ticks
        # in between did NOT re-attempt.
        self.assertEqual(app.complete_setup.call_count, 2)
        app.logger.exception.assert_called_once()
        # The rollback is what let the corrected value win: without it the
        # stale 'bad-token' in the process env would outrank settings.json
        # forever.
        self.assertEqual(os.environ.get('YOUTRACK_API_TOKEN'), 'good-token')
        # The final success cleared the failure the UI was showing.
        self.assertEqual(app.planning_flask_app.config['SETUP_ERROR'], '')

    def test_blank_inherited_env_key_does_not_shadow_the_wizard(self) -> None:
        """A blank ``KEY=`` inherited from the spawning shell lands
        as set-but-EMPTY process env vars. Empty means unset everywhere else
        (validators, settings UI, effective_config_env) — the transition's
        env load must overwrite them or the wizard's saved value would be
        shadowed forever and kato could never start."""
        from kato_core_lib.main import _wait_until_configured_then_finish_setup
        os.environ['YOUTRACK_API_TOKEN'] = ''   # restored by patch.dict stop
        self._write_settings()                  # wizard saved 'yt-token'
        service = object()
        app = self._app(None)
        app.complete_setup = Mock(side_effect=lambda: setattr(app, 'service', service))

        result = _wait_until_configured_then_finish_setup(
            app, sleep_fn=lambda _s: None, max_ticks=3,
        )

        self.assertTrue(result)
        # The blank was replaced by the operator's saved value, so hydra's
        # ${oc.env:...} reads resolve to the real token.
        self.assertEqual(os.environ.get('YOUTRACK_API_TOKEN'), 'yt-token')

    def test_unchanged_config_is_retried_periodically_after_a_failure(self) -> None:
        """A transient failure (provider blip) with an unchanged config must
        not wedge setup mode forever — the loop re-attempts every
        ``_SETUP_RETRY_EVERY_TICKS`` even without a settings change."""
        from kato_core_lib.main import _wait_until_configured_then_finish_setup
        self._write_settings()
        app = self._app(Mock(side_effect=[RuntimeError('blip'), None]))

        with patch('kato_core_lib.main._SETUP_RETRY_EVERY_TICKS', 2):
            result = _wait_until_configured_then_finish_setup(
                app, sleep_fn=lambda _s: None, max_ticks=6,
            )

        self.assertTrue(result)
        self.assertEqual(app.complete_setup.call_count, 2)

    def test_security_gates_block_a_bypass_flag_saved_during_setup(self) -> None:
        """SECURITY: the boot gates validated the env at startup — a
        gate-relevant flag saved through the UI afterwards must clear the
        SAME bar. Bypass-permissions without its requirements must refuse
        the transition: no service starts, the flag is rolled back out of
        the env, and the wizard sees why."""
        from kato_core_lib.main import _wait_until_configured_then_finish_setup
        self._write_settings({'KATO_CLAUDE_BYPASS_PERMISSIONS': 'true'})
        complete_setup = Mock()
        app = self._app(complete_setup)

        result = _wait_until_configured_then_finish_setup(
            app, sleep_fn=lambda _s: None, max_ticks=3,
        )

        self.assertFalse(result)
        # The gate fired BEFORE anything could start.
        complete_setup.assert_not_called()
        # Rolled back: the refused flag must not linger in the process env.
        self.assertIsNone(os.environ.get('KATO_CLAUDE_BYPASS_PERMISSIONS'))
        # The wizard shows the refusal.
        self.assertTrue(app.planning_flask_app.config['SETUP_ERROR'])
        self.assertTrue(app.planning_flask_app.config['NEEDS_CONFIG'])

    def test_backend_change_restarts_kato_in_place(self) -> None:
        """The no-terminal promise: picking a different agent backend in the
        wizard cannot be applied to the live managers, so kato re-execs
        itself instead of telling the operator to run a command."""
        from kato_core_lib.errors import AgentBackendChangedError
        from kato_core_lib.main import _wait_until_configured_then_finish_setup
        self._write_settings({
            'KATO_AGENT_BACKEND': 'claude',
            # Satisfy the claude-CLI presence check without a real binary on
            # PATH (CI has none); this test is about the backend-change restart.
            'KATO_CLAUDE_BINARY': sys.executable,
        })
        app = self._app(Mock(side_effect=AgentBackendChangedError(
            'agent backend changed (openhands → claude) …',
        )))

        with patch('kato_core_lib.main._restart_in_place') as restart:
            result = _wait_until_configured_then_finish_setup(
                app, sleep_fn=lambda _s: None, max_ticks=1,
            )

        self.assertFalse(result)  # (mocked exec returns; loop keeps waiting)
        restart.assert_called_once_with(app)
        # The UI learns WHY the page is about to reconnect.
        self.assertIn(
            'restarting', app.planning_flask_app.config['SETUP_ERROR'],
        )

    def test_backend_change_falls_back_to_setup_error_when_exec_fails(self) -> None:
        from kato_core_lib.errors import AgentBackendChangedError
        from kato_core_lib.main import _wait_until_configured_then_finish_setup
        self._write_settings({
            'KATO_AGENT_BACKEND': 'claude',
            'KATO_CLAUDE_BINARY': sys.executable,
        })
        app = self._app(Mock(side_effect=AgentBackendChangedError('changed')))

        with patch(
            'kato_core_lib.main._restart_in_place',
            side_effect=OSError('exec failed'),
        ):
            result = _wait_until_configured_then_finish_setup(
                app, sleep_fn=lambda _s: None, max_ticks=2,
            )

        self.assertFalse(result)
        # Loud fallback: the operator is told to restart manually.
        app.logger.exception.assert_called()
        # The loaded env was rolled back so nothing is left half-applied.
        self.assertIsNone(os.environ.get('KATO_AGENT_BACKEND'))

    def test_failed_attempt_surfaces_the_error_to_the_webserver(self) -> None:
        """The terminal must not be the only place that knows the start
        failed: the wizard polls /api/config-status, which reads the live
        Flask SETUP_ERROR this loop publishes."""
        from kato_core_lib.main import _wait_until_configured_then_finish_setup
        self._write_settings({'YOUTRACK_API_TOKEN': 'bad-token'})
        app = self._app(Mock(side_effect=RuntimeError('startup dependency validation failed: youtrack')))

        result = _wait_until_configured_then_finish_setup(
            app, sleep_fn=lambda _s: None, max_ticks=3,
        )

        self.assertFalse(result)
        self.assertIn(
            'startup dependency validation failed: youtrack',
            app.planning_flask_app.config['SETUP_ERROR'],
        )


class MarkWebserverConfiguredTests(unittest.TestCase):
    def test_noop_when_webserver_never_started(self) -> None:
        from kato_core_lib.main import _mark_webserver_configured
        _mark_webserver_configured(types.SimpleNamespace(logger=Mock()))  # no raise

    def test_flips_live_flask_config(self) -> None:
        from kato_core_lib.main import _mark_webserver_configured
        service = object()
        flask_app = types.SimpleNamespace(
            config={'AGENT_SERVICE': None, 'NEEDS_CONFIG': True},
        )
        app = types.SimpleNamespace(
            logger=Mock(), planning_flask_app=flask_app, service=service,
        )

        _mark_webserver_configured(app)

        self.assertIs(flask_app.config['AGENT_SERVICE'], service)
        self.assertFalse(flask_app.config['NEEDS_CONFIG'])

    def test_drops_the_agent_version_probe_cached_during_setup(self) -> None:
        """A probe cached in setup mode saw no backend settings yet.

        It reported "claude not found on PATH" (binary unresolvable), which
        outlived the transition and greeted a freshly configured instance with
        a false banner + no Upgrade button until a manual Refresh.
        """
        from kato_core_lib.main import _mark_webserver_configured
        flask_app = types.SimpleNamespace(config={
            'AGENT_SERVICE': None,
            'NEEDS_CONFIG': True,
            'AGENT_VERSION_INFO': {'found': False, 'can_upgrade': False,
                                   'detail': 'claude not found on PATH'},
        })
        app = types.SimpleNamespace(
            logger=Mock(), planning_flask_app=flask_app, service=object(),
        )

        _mark_webserver_configured(app)

        self.assertNotIn('AGENT_VERSION_INFO', flask_app.config)

    def test_missing_version_cache_is_not_an_error(self) -> None:
        from kato_core_lib.main import _mark_webserver_configured
        flask_app = types.SimpleNamespace(
            config={'AGENT_SERVICE': None, 'NEEDS_CONFIG': True},
        )
        app = types.SimpleNamespace(
            logger=Mock(), planning_flask_app=flask_app, service=object(),
        )

        _mark_webserver_configured(app)  # no KeyError

        self.assertFalse(flask_app.config['NEEDS_CONFIG'])


class RestartInPlaceTests(unittest.TestCase):
    """The backend-switch restart. Supervised (normal ``kato up``) exits
    with the launcher's restart code — a clean teardown is the only
    port-release guarantee on every platform. Exec is only the fallback
    for unsupervised direct runs."""

    def test_supervised_run_exits_with_the_restart_code(self) -> None:
        from kato_core_lib.main import _restart_in_place, _RESTART_EXIT_CODE
        app = types.SimpleNamespace(logger=Mock())
        with patch.dict('os.environ', {'KATO_SUPERVISED_RESTART': '1'}), \
             patch('os._exit') as fake_exit, patch('os.execv') as fake_exec:
            _restart_in_place(app)
        fake_exit.assert_called_once_with(_RESTART_EXIT_CODE)
        fake_exec.assert_not_called()

    def test_unsupervised_run_falls_back_to_exec(self) -> None:
        from kato_core_lib.main import _restart_in_place
        app = types.SimpleNamespace(logger=Mock())
        with patch.dict('os.environ', {}, clear=False):
            os.environ.pop('KATO_SUPERVISED_RESTART', None)
            with patch('os.execv') as fake_exec, patch('os._exit') as fake_exit:
                _restart_in_place(app)
        fake_exec.assert_called_once()
        fake_exit.assert_not_called()

    def test_werkzeug_fd_marker_never_leaks_into_the_next_process(self) -> None:
        from kato_core_lib.main import _restart_in_place
        app = types.SimpleNamespace(logger=Mock())
        with patch.dict('os.environ', {
            'KATO_SUPERVISED_RESTART': '1',
            'WERKZEUG_SERVER_FD': '7',
        }), patch('os._exit'):
            _restart_in_place(app)
            self.assertIsNone(os.environ.get('WERKZEUG_SERVER_FD'))


class ServeFlaskBindRetryTests(unittest.TestCase):
    """The webserver must survive a port-release race after a restart —
    Werkzeug raises SystemExit on a busy port, which a plain
    ``except Exception`` misses."""

    def test_retries_busy_port_then_serves(self) -> None:
        from kato_core_lib.main import _serve_flask_with_bind_retry
        flask_app = Mock()
        flask_app.run.side_effect = [SystemExit(1), OSError('in use'), None]
        logger = Mock()

        _serve_flask_with_bind_retry(
            flask_app, '127.0.0.1', 5050, logger, sleep_fn=lambda _s: None,
        )

        self.assertEqual(flask_app.run.call_count, 3)
        logger.error.assert_not_called()
        # The dotenv trap stays closed on every attempt.
        self.assertFalse(flask_app.run.call_args.kwargs['load_dotenv'])

    def test_gives_up_loudly_after_the_attempt_budget(self) -> None:
        from kato_core_lib.main import _serve_flask_with_bind_retry
        flask_app = Mock()
        flask_app.run.side_effect = SystemExit(1)
        logger = Mock()

        _serve_flask_with_bind_retry(
            flask_app, '127.0.0.1', 5050, logger,
            sleep_fn=lambda _s: None, attempts=3,
        )

        self.assertEqual(flask_app.run.call_count, 3)
        logger.error.assert_called_once()

    def test_non_bind_crash_is_logged_not_retried(self) -> None:
        from kato_core_lib.main import _serve_flask_with_bind_retry
        flask_app = Mock()
        flask_app.run.side_effect = RuntimeError('boom')
        logger = Mock()

        _serve_flask_with_bind_retry(
            flask_app, '127.0.0.1', 5050, logger, sleep_fn=lambda _s: None,
        )

        self.assertEqual(flask_app.run.call_count, 1)
        logger.exception.assert_called_once()

    def test_ssl_context_is_forwarded_to_flask_run(self) -> None:
        from kato_core_lib.main import _serve_flask_with_bind_retry
        flask_app = Mock()
        logger = Mock()

        _serve_flask_with_bind_retry(
            flask_app, '127.0.0.1', 5050, logger,
            sleep_fn=lambda _s: None, ssl_context=('cert.pem', 'key.pem'),
        )

        self.assertEqual(
            flask_app.run.call_args.kwargs['ssl_context'], ('cert.pem', 'key.pem'),
        )

    def test_ssl_context_defaults_to_none_for_plain_http(self) -> None:
        from kato_core_lib.main import _serve_flask_with_bind_retry
        flask_app = Mock()
        logger = Mock()

        _serve_flask_with_bind_retry(
            flask_app, '127.0.0.1', 5050, logger, sleep_fn=lambda _s: None,
        )

        self.assertIsNone(flask_app.run.call_args.kwargs['ssl_context'])


class ResolveWebserverTlsTests(unittest.TestCase):
    """``KATO_WEBSERVER_HTTPS`` gate + graceful fallback to plain HTTP."""

    def test_disabled_via_env_returns_http_with_no_context(self) -> None:
        from kato_core_lib.main import _resolve_webserver_tls
        with patch.dict('os.environ', {'KATO_WEBSERVER_HTTPS': '0'}):
            scheme, ssl_context = _resolve_webserver_tls(Mock())
        self.assertEqual(scheme, 'http')
        self.assertIsNone(ssl_context)

    def test_enabled_by_default_uses_https_when_cert_available(self) -> None:
        from kato_core_lib.main import _resolve_webserver_tls
        with patch.dict('os.environ', {}, clear=False), \
             patch(
                 'kato_core_lib.helpers.tls_cert_utils.ensure_local_tls_cert',
                 return_value=('cert.pem', 'key.pem'),
             ):
            os.environ.pop('KATO_WEBSERVER_HTTPS', None)
            scheme, ssl_context = _resolve_webserver_tls(Mock())
        self.assertEqual(scheme, 'https')
        self.assertEqual(ssl_context, ('cert.pem', 'key.pem'))

    def test_falls_back_to_http_when_cert_generation_fails(self) -> None:
        from kato_core_lib.main import _resolve_webserver_tls
        with patch.dict('os.environ', {}, clear=False), \
             patch(
                 'kato_core_lib.helpers.tls_cert_utils.ensure_local_tls_cert',
                 return_value=None,
             ):
            os.environ.pop('KATO_WEBSERVER_HTTPS', None)
            scheme, ssl_context = _resolve_webserver_tls(Mock())
        self.assertEqual(scheme, 'http')
        self.assertIsNone(ssl_context)


class HealthzSslContextTests(unittest.TestCase):
    """The internal healthz poll must trust kato's OWN local CA (never
    skip verification) once HTTPS is on, and stay a no-op in
    plain-HTTP mode."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        patcher = patch.dict('os.environ', {'KATO_TLS_DIR': self._td.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_returns_none_when_no_cert_exists_yet(self) -> None:
        from kato_core_lib.main import _healthz_ssl_context
        self.assertIsNone(_healthz_ssl_context())

    def test_returns_a_context_trusting_the_generated_ca(self) -> None:
        import ssl
        from kato_core_lib.helpers.tls_cert_utils import ensure_local_tls_cert
        from kato_core_lib.main import _healthz_ssl_context
        ensure_local_tls_cert()
        context = _healthz_ssl_context()
        self.assertIsNotNone(context)
        # A real ssl.SSLContext — verification is still ON (this must
        # never be ssl.CERT_NONE, which would trust ANY server).
        self.assertNotEqual(context.verify_mode, ssl.CERT_NONE)

    def test_returns_none_on_unexpected_error(self) -> None:
        from kato_core_lib.main import _healthz_ssl_context
        with patch(
            'kato_core_lib.helpers.tls_cert_utils.ca_paths',
            side_effect=RuntimeError('boom'),
        ):
            self.assertIsNone(_healthz_ssl_context())


class PlanningWebserverNoDotenvTests(unittest.TestCase):
    def test_flask_run_does_not_load_dotenv(self) -> None:
        """Flask's ``run()`` silently loads ``<cwd>/.env`` into os.environ
        when python-dotenv is installed — a hidden third config path.
        kato reads ONLY ~/.kato/settings.json (+ real shell env), so the
        webserver spawn must pin ``load_dotenv=False``. Without this pin,
        an operator's stale .env would leak into the setup-mode transition
        (caught live: the wait loop saw a "complete" config sourced from a
        file kato is supposed to ignore)."""
        import inspect
        from kato_core_lib import main as main_module
        src = inspect.getsource(main_module._serve_flask_with_bind_retry)
        self.assertIn('load_dotenv=False', src)


class PlanningWebserverDoubleStartGuardTests(unittest.TestCase):
    def test_second_start_is_a_noop_when_already_serving(self) -> None:
        # After the setup→running fall-through, the full boot path calls the
        # webserver helper again. It must reuse the live thread — a second
        # bind on the port would fail with "address already in use".
        from kato_core_lib.main import _start_planning_webserver_if_enabled
        app = types.SimpleNamespace(
            logger=Mock(), planning_flask_app=object(),
        )

        _start_planning_webserver_if_enabled(app)

        # Immediate return: no "disabled"/"skipped"/"listening" logging, no
        # attribute probing beyond the guard.
        app.logger.info.assert_not_called()
        app.logger.warning.assert_not_called()


if __name__ == '__main__':
    unittest.main()
