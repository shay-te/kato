import unittest
from unittest.mock import patch


from kato_core_lib.kato_instance import KatoInstance
from tests.utils import build_test_cfg


class KatoInstanceTests(unittest.TestCase):
    def tearDown(self) -> None:
        KatoInstance._app_instance = None

    def test_get_raises_before_init(self) -> None:
        KatoInstance._app_instance = None

        with self.assertRaisesRegex(RuntimeError, 'KatoCoreLib is not initialized'):
            KatoInstance.get()

    def test_init_is_idempotent(self) -> None:
        cfg = build_test_cfg()
        with patch('kato_core_lib.kato_core_lib.EmailCoreLib'), patch(
            'kato_core_lib.kato_core_lib.AgentService.validate_connections'
        ):
            KatoInstance.init(cfg)
            first = KatoInstance.get()
            KatoInstance.init(cfg)
            second = KatoInstance.get()

        self.assertIs(first, second)

    def test_configured_boot_builds_the_agent_service(self) -> None:
        cfg = build_test_cfg()
        with patch('kato_core_lib.kato_core_lib.EmailCoreLib'), patch(
            'kato_core_lib.kato_core_lib.AgentService.validate_connections'
        ) as validate:
            KatoInstance.init(cfg)  # setup_mode defaults to False
            app = KatoInstance.get()

        self.assertFalse(app.needs_config)
        self.assertIsNotNone(app.service)
        validate.assert_called_once_with()

    def test_setup_mode_skips_agent_service_but_wires_ui_managers(self) -> None:
        """The unconfigured-boot contract: build ONLY the backend-agnostic
        managers the planning UI needs, never the creds-dependent agent
        service or connection validation. A call to either would blow up
        boot for an operator who hasn't configured yet — the exact crash
        this mode exists to prevent."""
        cfg = build_test_cfg()
        # Tripwires: if setup mode ever reaches these, the test fails loudly
        # instead of silently regressing to the crash-on-missing-creds path.
        with patch(
            'kato_core_lib.kato_core_lib.KatoCoreLib._build_agent_service',
        ) as build_service, patch(
            'kato_core_lib.kato_core_lib.AgentService.validate_connections',
        ) as validate:
            KatoInstance.init(cfg, setup_mode=True)
            app = KatoInstance.get()

        self.assertTrue(app.needs_config)
        self.assertIsNone(app.service)
        build_service.assert_not_called()
        validate.assert_not_called()
        # The planning UI needs a workspace manager to render even when
        # unconfigured, so the operator has a browser tab to configure from.
        # (``session_manager`` is Claude-specific and is legitimately None
        # for the openhands-backed test config — ``workspace_manager`` is the
        # backend-agnostic one both transports rely on.)
        self.assertIsNotNone(app.workspace_manager)

    def test_complete_setup_builds_service_and_reuses_ui_managers(self) -> None:
        """The terminal-free apply: a setup-mode instance later completes
        setup IN-PROCESS. The agent service comes up, connection validation
        runs, and — critically — the managers built at setup boot are the
        SAME objects afterwards, because the planning webserver captured
        those references at create_app time."""
        cfg = build_test_cfg()
        with patch('kato_core_lib.kato_core_lib.EmailCoreLib'), patch(
            'kato_core_lib.kato_core_lib.AgentService.validate_connections'
        ) as validate:
            KatoInstance.init(cfg, setup_mode=True)
            app = KatoInstance.get()
            manager_before = app.workspace_manager
            runner_before = app.planning_session_runner

            app.complete_setup()

        self.assertFalse(app.needs_config)
        self.assertIsNotNone(app.service)
        validate.assert_called_once_with()
        self.assertIs(app.workspace_manager, manager_before)
        self.assertIs(app.planning_session_runner, runner_before)

    def test_complete_setup_failure_keeps_setup_mode(self) -> None:
        """Bad credentials (connection validation raises) must leave the
        instance exactly as it was: no service, still needs_config — so the
        wait loop can retry after the operator fixes the values."""
        cfg = build_test_cfg()
        with patch('kato_core_lib.kato_core_lib.EmailCoreLib'), patch(
            'kato_core_lib.kato_core_lib.AgentService.validate_connections',
            side_effect=RuntimeError('bad creds'),
        ):
            KatoInstance.init(cfg, setup_mode=True)
            app = KatoInstance.get()
            with self.assertRaisesRegex(RuntimeError, 'bad creds'):
                app.complete_setup()

        self.assertTrue(app.needs_config)
        self.assertIsNone(app.service)

    def test_complete_setup_refuses_a_mid_setup_backend_switch(self) -> None:
        """The setup-boot managers are backend-shaped and the webserver holds
        references to them — a backend switched in Settings during setup
        cannot be applied live. It must raise the TYPED error (main's wait
        loop catches it by identity to self-restart), never run mis-wired."""
        from kato_core_lib.errors import AgentBackendChangedError
        cfg = build_test_cfg()
        with patch('kato_core_lib.kato_core_lib.EmailCoreLib'), patch(
            'kato_core_lib.kato_core_lib.AgentService.validate_connections'
        ):
            KatoInstance.init(cfg, setup_mode=True)  # managers built for openhands
            app = KatoInstance.get()
            with patch(
                'kato_core_lib.kato_core_lib.resolved_agent_backend',
                return_value='claude',
            ):
                with self.assertRaisesRegex(
                    AgentBackendChangedError, 'restarts itself',
                ):
                    app.complete_setup()

        self.assertTrue(app.needs_config)
        self.assertIsNone(app.service)

    def test_setup_boot_survives_an_unsupported_agent_backend(self) -> None:
        """A typo'd KATO_AGENT_BACKEND is exactly the kind of problem the
        setup UI exists to fix — it must not crash the setup boot into a
        traceback (no managers → no webserver → no way to fix it)."""
        cfg = build_test_cfg()
        with patch('kato_core_lib.kato_core_lib.EmailCoreLib'), patch(
            'kato_core_lib.kato_core_lib.resolved_agent_backend',
            side_effect=[ValueError('unsupported KATO_AGENT_BACKEND: claud'), 'openhands'],
        ):
            KatoInstance.init(cfg, setup_mode=True)
            app = KatoInstance.get()

        self.assertTrue(app.needs_config)
        # The UI-only managers came up on the fallback backend.
        self.assertIsNotNone(app.workspace_manager)

    def test_complete_setup_validates_before_wiring_the_done_callback(self) -> None:
        # Ordering guard: a FAILED validation must leave no live wiring —
        # a done callback bound to a dead service would swallow real
        # completions after the operator fixes the config.
        import inspect
        from kato_core_lib.kato_core_lib import KatoCoreLib
        src = inspect.getsource(KatoCoreLib.complete_setup)
        self.assertLess(
            src.index('validate_connections'), src.index('set_done_callback'),
        )

    def test_complete_setup_is_a_noop_on_a_configured_instance(self) -> None:
        cfg = build_test_cfg()
        with patch('kato_core_lib.kato_core_lib.EmailCoreLib'), patch(
            'kato_core_lib.kato_core_lib.AgentService.validate_connections'
        ) as validate:
            KatoInstance.init(cfg)  # normal, configured boot
            app = KatoInstance.get()
            service_before = app.service

            app.complete_setup()

        self.assertIs(app.service, service_before)
        # Only the boot-time validation ran — no second round.
        validate.assert_called_once_with()
