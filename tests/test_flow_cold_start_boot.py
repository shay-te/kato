"""Flow #1 — Cold-start boot.

A-Z scenario:

    1. Operator runs ``kato run --config conf/config.yaml``.
    2. ``main(cfg)`` validates environment, sandbox, TLS pin, docker
       (when enabled).
    3. ``KatoInstance.init(cfg)`` constructs the service graph and
       runs the parallel startup dependency validators (repo + task
       client + impl + testing).
    4. **UI-FIRST**: ``_load_hooks_or_refuse`` (local, fail-closed) then
       ``_start_planning_webserver_if_enabled`` bring the UI up in ~seconds —
       BEFORE the network-bound connection validation, not after.
    5. A background finalize thread (``_finalize_configured_boot``) then:
        a. validates connections (retrying + surfacing the error in the UI
           instead of exiting on failure),
        b. runs ``_run_boot_reconciliation`` — orphan recovery, branch
           reconcile, stuck-status reset, comment requeue, done-task cleanup,
        c. marks the webserver fully configured,
        d. starts the post-boot workers (incl. ``_warm_up_repository_inventory``),
        e. sets ``ready_event`` to release the scan loop.
    6. **NO auto-spawn of past sessions** (Bug 1 fix).
    7. ``_run_task_scan_loop`` waits on ``ready_event`` before its first tick.

The order matters: the webserver must come up BEFORE validation (that is the
whole UI-first point — the desktop splash no longer waits the ~minute
validation tail), reconciliation must finish before the scan loop is released,
and the no-auto-spawn must remain enforced (Bug 1 territory). All pinned below.

What this file does NOT do: instantiate the real ``KatoInstance`` or
``KatoCoreLib`` with their full service graph — that requires a real
config + every connection. We test the BOOT WIRING via the source-
inspection contract (the order helpers are called in ``main``) and
via direct invocation of the helper functions with mocked apps. Both
are bug-finding without needing a real boot.
"""

from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_core_lib import main as kato_main


class FlowColdStartBootMainSourceTests(unittest.TestCase):
    """Source-inspection guards: lock the order and presence of every
    boot-time call by reading the source of ``main()``.

    Source inspection is brittle on whitespace but EXACTLY the right
    tool for "this function calls X before Y" contracts that don't
    have a clean side-effect to assert against.
    """

    def setUp(self) -> None:
        self.src = inspect.getsource(kato_main.main)
        # The reconciliation / worker steps moved into helpers on the UI-first
        # boot; inspect those too.
        self.recon = inspect.getsource(kato_main._run_boot_reconciliation)
        self.workers = inspect.getsource(kato_main._start_post_boot_workers)
        self.finalize = inspect.getsource(kato_main._finalize_configured_boot)

    def test_flow_boot_calls_orphan_recovery(self) -> None:
        self.assertIn('_recover_orphan_workspaces(app)', self.recon)

    def test_flow_boot_calls_branch_reconciliation(self) -> None:
        self.assertIn('_reconcile_workspace_branches(app)', self.recon)

    def test_flow_boot_calls_stuck_status_reset(self) -> None:
        self.assertIn('_reset_stuck_workspace_statuses(app)', self.recon)

    def test_flow_boot_does_not_call_resume_streaming_sessions(self) -> None:
        # Bug 1's smoking gun. If THIS test fails, kato is auto-spawning
        # all past Claude sessions at boot — burning tokens, surprising
        # the operator with a thundering herd of subprocesses.
        self.assertNotIn(
            '_resume_streaming_sessions(app)', self.src,
            'main() is auto-spawning sessions at boot again (Bug 1 regression)',
        )

    def test_flow_boot_calls_planning_webserver_starter(self) -> None:
        self.assertIn('_start_planning_webserver_if_enabled(app)', self.src)

    def test_flow_boot_registers_shutdown_hook(self) -> None:
        # Without the shutdown hook, kato leaks subprocesses on
        # Ctrl-C (Claude sessions stay alive in zombie state).
        self.assertIn('_register_shutdown_hook(app)', self.src)

    def test_flow_boot_warms_up_repository_inventory(self) -> None:
        # Without warm-up, the FIRST task pickup pays the disk-walk
        # cost (can be seconds on a large workspaces root). Warm-up
        # runs the walk in background — now part of the post-boot workers.
        self.assertIn('_warm_up_repository_inventory(app)', self.workers)

    def test_flow_boot_runs_task_scan_loop(self) -> None:
        self.assertIn('_run_task_scan_loop(', self.src)

    def test_flow_boot_webserver_starts_before_validation(self) -> None:
        # UI-FIRST contract: the configured boot serves the webserver BEFORE
        # the background finalize that validates connections — so the desktop
        # splash appears in ~seconds instead of after the ~minute validation
        # tail. rindex → the fast-path serve (setup mode has earlier ones);
        # match the actual finalize spawn call, not the comment referencing it.
        webserver_idx = self.src.rindex('_start_planning_webserver_if_enabled(app)')
        finalize_idx = self.src.index('_finalize_configured_boot(app')
        self.assertLess(
            webserver_idx, finalize_idx,
            'validation runs before the webserver — the UI-first win is lost',
        )

    def test_flow_boot_reconcile_finishes_before_scan_release(self) -> None:
        # The finalize runs the full reconciliation and starts the post-boot
        # workers before it sets ready_event (which releases the scan loop),
        # so no autonomous scan ever runs against a half-reconciled state.
        self.assertLess(
            self.finalize.index('_run_boot_reconciliation(app)'),
            self.finalize.index('ready_event.set()'),
        )
        self.assertLess(
            self.finalize.index('_start_post_boot_workers(app)'),
            self.finalize.index('ready_event.set()'),
        )

    def test_flow_boot_branch_reconcile_runs_after_orphan_recovery(self) -> None:
        # Branch reconcile assumes the workspace records exist —
        # orphan recovery is what creates them. Reversing the order
        # leaves real workspaces with their branches not reconciled.
        recovery_idx = self.recon.index('_recover_orphan_workspaces(app)')
        reconcile_idx = self.recon.index('_reconcile_workspace_branches(app)')
        self.assertLess(recovery_idx, reconcile_idx)

    def test_flow_boot_validation_deferred_but_service_built_at_init(self) -> None:
        # ``KatoInstance.init`` builds the service with defer_validation=True:
        # the service graph exists (so the webserver has something to serve)
        # but the network connection checks are left for the background
        # finalize — the mechanism that lets the UI come up first.
        self.assertIn('defer_validation=True', self.src)
        init_idx = self.src.index('KatoInstance.init(cfg')
        finalize_idx = self.src.index('_finalize_configured_boot(app')
        self.assertLess(init_idx, finalize_idx)

    def test_flow_boot_warm_up_runs_before_scan_loop(self) -> None:
        # Warm-up is fire-and-forget background — but it must be KICKED OFF
        # before the scan loop's first tick or the loop waits on a cold cache.
        # It now runs inside the finalize's post-boot workers, which complete
        # before ready_event releases the loop.
        self.assertLess(
            self.finalize.index('_start_post_boot_workers(app)'),
            self.finalize.index('ready_event.set()'),
        )


# ---------------------------------------------------------------------------
# Direct helper invocation: do the boot helpers handle empty / errored apps?
# ---------------------------------------------------------------------------


class FlowColdStartBootHelperRobustnessTests(unittest.TestCase):
    """Each boot helper should fail-safe when its service is missing,
    raises, or returns nothing useful. Otherwise a single broken
    dependency takes the whole process down before the webserver
    can come up and surface the error."""

    def test_flow_boot_recover_orphan_workspaces_handles_no_service(self) -> None:
        # App with no workspace_recovery_service: helper should not
        # raise.
        app = SimpleNamespace(logger=MagicMock())
        try:
            kato_main._recover_orphan_workspaces(app)
        except AttributeError:
            self.fail(
                '_recover_orphan_workspaces crashed when service was missing '
                '— boot would never reach the webserver',
            )
        except Exception as exc:
            # Any exception is suspicious — boot helpers should be
            # safety-padded. But we accept that some implementations
            # log-and-swallow; the regression we're catching is hard
            # AttributeError-during-attribute-access.
            self.assertNotIsInstance(exc, AttributeError)

    def test_flow_boot_recover_orphan_workspaces_handles_recovery_exception(self) -> None:
        # If the recovery service itself raises (e.g., disk perms),
        # the helper should log and continue rather than killing
        # the boot.
        recovery = MagicMock()
        recovery.recover_orphan_workspaces.side_effect = RuntimeError('disk perms')
        app = SimpleNamespace(
            workspace_recovery_service=recovery,
            service=SimpleNamespace(workspace_recovery_service=recovery),
            logger=MagicMock(),
        )
        try:
            kato_main._recover_orphan_workspaces(app)
        except RuntimeError:
            self.fail(
                'orphan recovery exception killed boot — webserver never '
                'comes up, operator has no UI to see the error',
            )


if __name__ == '__main__':
    unittest.main()
