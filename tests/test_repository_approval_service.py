"""Tests for the Restricted Execution Protocol approval service.

Pin down two surfaces:

1. The JSON-sidecar layer (``RepositoryApprovalService``) round-trips
   approvals correctly: idempotent re-approval, revocation, mode
   upgrade, atomic write, corrupt-file tolerance, lookups.
2. The preflight gate fires when an approved repo is missing and
   stays out of the way when every repo is approved or REP is
   disabled.

The CLI shim (``scripts/approve_repository.py``) is exercised by
mocking the underlying service — we don't need to write to the host's
real ``~/.kato`` directory.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kato_core_lib.data_layers.data.repository_approval import (
    ApprovalMode,
    ApprovalSidecar,
    RepositoryApproval,
)
from kato_core_lib.data_layers.service.repository_approval_service import (
    APPROVED_REPOSITORIES_PATH_ENV_KEY,
    OPERATOR_EMAIL_ENV_KEY,
    RepositoryApprovalService,
    RestrictedExecutionRefusal,
    RestrictedModePostureViolation,
    RuntimePosture,
    operator_identity,
    restricted_mode_posture_violations,
)


def _make_service(tmpdir: Path) -> RepositoryApprovalService:
    return RepositoryApprovalService(
        storage_path=tmpdir / 'approved-repositories.json',
    )


class ApprovalSidecarRoundTripTests(unittest.TestCase):
    """Approve / revoke / read against a real on-disk sidecar."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def test_unwritten_sidecar_lists_no_approvals(self) -> None:
        service = _make_service(self.tmpdir)
        self.assertEqual(service.list_approvals(), ())
        self.assertIsNone(service.is_approved('any-repo'))

    def test_approve_writes_sidecar_with_normalised_id(self) -> None:
        service = _make_service(self.tmpdir)
        entry = service.approve(
            'External-Vendor-SDK',
            remote_url='git@github.com:vendor/sdk.git',
        )
        self.assertEqual(entry.repository_id, 'external-vendor-sdk')
        self.assertEqual(entry.remote_url, 'git@github.com:vendor/sdk.git')
        self.assertEqual(entry.approval_mode, ApprovalMode.RESTRICTED)
        self.assertGreater(entry.approved_at_epoch, 0)

    def test_approval_persists_across_service_instances(self) -> None:
        first = _make_service(self.tmpdir)
        first.approve('x', remote_url='git@example.com:x.git')
        second = _make_service(self.tmpdir)
        self.assertEqual(second.is_approved('x'), ApprovalMode.RESTRICTED)

    def test_re_approval_with_same_inputs_is_no_op(self) -> None:
        service = _make_service(self.tmpdir)
        first = service.approve('x', remote_url='git@example.com:x.git')
        second = service.approve('x', remote_url='git@example.com:x.git')
        self.assertEqual(first.approved_at_epoch, second.approved_at_epoch)

    def test_re_approval_upgrades_mode(self) -> None:
        service = _make_service(self.tmpdir)
        service.approve('x', remote_url='git@example.com:x.git')
        upgraded = service.approve(
            'x', remote_url='git@example.com:x.git',
            mode=ApprovalMode.TRUSTED,
        )
        self.assertEqual(upgraded.approval_mode, ApprovalMode.TRUSTED)
        self.assertEqual(service.is_approved('x'), ApprovalMode.TRUSTED)

    def test_re_approval_with_new_remote_url_updates_url(self) -> None:
        service = _make_service(self.tmpdir)
        service.approve('x', remote_url='git@old:x.git')
        updated = service.approve('x', remote_url='git@new:x.git')
        self.assertEqual(updated.remote_url, 'git@new:x.git')
        self.assertEqual(service.lookup('x').remote_url, 'git@new:x.git')

    def test_revoke_removes_entry(self) -> None:
        service = _make_service(self.tmpdir)
        service.approve('x', remote_url='git@example.com:x.git')
        self.assertTrue(service.revoke('x'))
        self.assertIsNone(service.is_approved('x'))

    def test_revoke_returns_false_when_no_entry(self) -> None:
        service = _make_service(self.tmpdir)
        self.assertFalse(service.revoke('does-not-exist'))

    def test_revoke_is_case_insensitive(self) -> None:
        service = _make_service(self.tmpdir)
        service.approve('My-Repo', remote_url='git@example.com:x.git')
        self.assertTrue(service.revoke('MY-REPO'))

    def test_is_approved_match_is_case_insensitive(self) -> None:
        service = _make_service(self.tmpdir)
        service.approve('My-Repo', remote_url='git@example.com:x.git')
        self.assertEqual(service.is_approved('MY-REPO'), ApprovalMode.RESTRICTED)

    def test_unapproved_ids_filters_correctly(self) -> None:
        service = _make_service(self.tmpdir)
        service.approve('approved-repo', remote_url='git@example.com:a.git')
        repos = [
            SimpleNamespace(id='approved-repo'),
            SimpleNamespace(id='unapproved-repo'),
        ]
        self.assertEqual(
            service.unapproved_repository_ids(repos),
            ['unapproved-repo'],
        )

    def test_corrupt_sidecar_is_treated_as_empty(self) -> None:
        path = self.tmpdir / 'approved-repositories.json'
        path.write_text('{not valid json', encoding='utf-8')
        service = _make_service(self.tmpdir)
        self.assertEqual(service.list_approvals(), ())
        self.assertIsNone(service.is_approved('anything'))

    def test_sidecar_with_non_dict_payload_is_treated_as_empty(self) -> None:
        path = self.tmpdir / 'approved-repositories.json'
        path.write_text(json.dumps([1, 2, 3]), encoding='utf-8')
        service = _make_service(self.tmpdir)
        self.assertEqual(service.list_approvals(), ())

    def test_atomic_write_does_not_leave_tmp_file_behind(self) -> None:
        service = _make_service(self.tmpdir)
        service.approve('x', remote_url='git@example.com:x.git')
        # Sidecar exists but no .tmp leftover.
        self.assertTrue((self.tmpdir / 'approved-repositories.json').exists())
        self.assertFalse(any(self.tmpdir.glob('*.tmp')))

    def test_concurrent_approvals_do_not_lose_entries(self) -> None:
        service = _make_service(self.tmpdir)

        def _approve(repo_id: str) -> None:
            service.approve(repo_id, remote_url=f'git@example.com:{repo_id}.git')

        threads = [
            threading.Thread(target=_approve, args=(f'repo-{n}',))
            for n in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        ids = sorted(entry.repository_id for entry in service.list_approvals())
        self.assertEqual(ids, [f'repo-{n}' for n in range(8)])


class DataModelTests(unittest.TestCase):
    """``RepositoryApproval`` / ``ApprovalSidecar`` JSON shape."""

    def test_approval_mode_from_string_falls_back_to_restricted(self) -> None:
        self.assertEqual(ApprovalMode.from_string(''), ApprovalMode.RESTRICTED)
        self.assertEqual(ApprovalMode.from_string('garbage'), ApprovalMode.RESTRICTED)
        self.assertEqual(ApprovalMode.from_string('TRUSTED'), ApprovalMode.TRUSTED)
        self.assertEqual(ApprovalMode.from_string('restricted'), ApprovalMode.RESTRICTED)

    def test_sidecar_round_trips(self) -> None:
        original = ApprovalSidecar(
            version=1,
            approved=(
                RepositoryApproval(
                    repository_id='x',
                    remote_url='git@example.com:x.git',
                    approved_at_epoch=1234.5,
                    approved_by='ops@example.com',
                    approval_mode=ApprovalMode.TRUSTED,
                ),
            ),
        )
        round_tripped = ApprovalSidecar.from_dict(original.to_dict())
        self.assertEqual(round_tripped, original)

    def test_sidecar_drops_entries_without_repository_id(self) -> None:
        sidecar = ApprovalSidecar.from_dict({
            'version': 1,
            'approved': [
                {'repository_id': '', 'remote_url': 'x'},
                {'repository_id': 'valid', 'remote_url': 'y'},
            ],
        })
        self.assertEqual([entry.repository_id for entry in sidecar.approved], ['valid'])


class EnvHelperTests(unittest.TestCase):
    """Module-level helpers backing the service constructor."""

    def test_operator_identity_prefers_explicit_email(self) -> None:
        self.assertEqual(
            operator_identity(env={OPERATOR_EMAIL_ENV_KEY: 'ops@example.com'}),
            'ops@example.com',
        )

    def test_operator_identity_falls_back_to_user(self) -> None:
        self.assertEqual(
            operator_identity(env={'USER': 'shay'}),
            'shay',
        )

    def test_operator_identity_returns_unknown_when_nothing_is_set(self) -> None:
        self.assertEqual(operator_identity(env={}), 'unknown')


class PreflightIntegrationTests(unittest.TestCase):
    """REP gate inside ``TaskPreflightService``.

    Builds a stub preflight harness so we can drive
    ``_enforce_restricted_execution_protocol`` directly without
    spinning up the full service graph.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def _build_preflight(self, approval_service, *, posture_supplier=None):
        from kato_core_lib.data_layers.service.task_preflight_service import (
            TaskPreflightService,
        )

        # Bypass the real constructor so we don't have to mock five
        # collaborators we don't exercise here.
        preflight = TaskPreflightService.__new__(TaskPreflightService)
        preflight._repository_approval_service = approval_service
        preflight._security_scanner_service = None
        preflight._workspace_provisioner = None
        preflight._runtime_posture_supplier = posture_supplier
        import logging
        preflight.logger = logging.getLogger('test-preflight')
        preflight._active_blocking_comment_log_state = {}
        return preflight

    def test_gate_passes_when_every_repo_is_approved(self) -> None:
        service = _make_service(self.tmpdir)
        service.approve('public-app', remote_url='git@example.com:public-app.git')
        preflight = self._build_preflight(service)
        task = SimpleNamespace(id='TASK-1', summary='', description='', tags=[])

        result = preflight._enforce_restricted_execution_protocol(
            task, [SimpleNamespace(id='public-app')],
        )

        self.assertTrue(result)

    def test_gate_refuses_when_a_repo_is_unapproved(self) -> None:
        service = _make_service(self.tmpdir)
        preflight = self._build_preflight(service)
        task = SimpleNamespace(id='TASK-2', summary='', description='', tags=[])
        captured: dict = {}

        def handler(failed_task, exc, prepared):
            captured['task'] = failed_task
            captured['exc'] = exc

        result = preflight._enforce_restricted_execution_protocol(
            task,
            [SimpleNamespace(id='unapproved')],
            failure_handler=handler,
        )

        self.assertFalse(result)
        self.assertIs(captured['task'], task)
        self.assertIsInstance(captured['exc'], RestrictedExecutionRefusal)
        self.assertEqual(captured['exc'].repository_ids, ['unapproved'])

    def test_gate_refusal_lists_every_unapproved_repo(self) -> None:
        service = _make_service(self.tmpdir)
        service.approve('approved', remote_url='git@example.com:approved.git')
        preflight = self._build_preflight(service)
        task = SimpleNamespace(id='TASK-3', summary='', description='', tags=[])
        captured: dict = {}

        def handler(failed_task, exc, prepared):
            captured['exc'] = exc

        preflight._enforce_restricted_execution_protocol(
            task,
            [
                SimpleNamespace(id='approved'),
                SimpleNamespace(id='unapproved-a'),
                SimpleNamespace(id='unapproved-b'),
            ],
            failure_handler=handler,
        )

        self.assertEqual(
            captured['exc'].repository_ids,
            ['unapproved-a', 'unapproved-b'],
        )

    def test_gate_no_op_when_service_not_wired(self) -> None:
        preflight = self._build_preflight(None)
        task = SimpleNamespace(id='TASK-5', summary='', description='', tags=[])

        result = preflight._enforce_restricted_execution_protocol(
            task, [SimpleNamespace(id='whatever')],
        )

        self.assertTrue(result)

    def test_gate_no_op_when_repository_list_is_empty(self) -> None:
        service = _make_service(self.tmpdir)
        preflight = self._build_preflight(service)
        task = SimpleNamespace(id='TASK-6', summary='', description='', tags=[])

        result = preflight._enforce_restricted_execution_protocol(task, [])

        self.assertTrue(result)


class PostureGateTests(unittest.TestCase):
    """Posture gate fires when RESTRICTED-mode repos meet a weak posture."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self._safe_posture = RuntimePosture(
            bypass_permissions=False,
            docker_mode_on=True,
            scanner_blocks_at_medium=True,
        )

    def _build_preflight(self, approval_service, posture):
        from kato_core_lib.data_layers.service.task_preflight_service import (
            TaskPreflightService,
        )

        preflight = TaskPreflightService.__new__(TaskPreflightService)
        preflight._repository_approval_service = approval_service
        preflight._security_scanner_service = None
        preflight._workspace_provisioner = None
        preflight._runtime_posture_supplier = (lambda: posture) if posture is not None else None
        import logging
        preflight.logger = logging.getLogger('test-preflight-posture')
        preflight._active_blocking_comment_log_state = {}
        return preflight

    def test_violations_helper_lists_no_violations_for_safe_posture(self) -> None:
        self.assertEqual(restricted_mode_posture_violations(self._safe_posture), [])

    def test_violations_helper_flags_each_weak_knob(self) -> None:
        weak = RuntimePosture(
            bypass_permissions=True,
            docker_mode_on=False,
            scanner_blocks_at_medium=False,
        )
        violations = restricted_mode_posture_violations(weak)
        self.assertEqual(len(violations), 3)
        joined = ' '.join(violations)
        self.assertIn('KATO_CLAUDE_BYPASS_PERMISSIONS', joined)
        self.assertIn('KATO_CLAUDE_DOCKER', joined)
        self.assertIn('block_on_severity', joined)

    def test_safe_posture_lets_restricted_repo_proceed(self) -> None:
        service = _make_service(self.tmpdir)
        service.approve('vendor', remote_url='git@example.com:vendor.git')
        preflight = self._build_preflight(service, self._safe_posture)
        task = SimpleNamespace(id='TASK-P1', summary='', description='', tags=[])

        result = preflight._enforce_restricted_execution_protocol(
            task, [SimpleNamespace(id='vendor')],
        )

        self.assertTrue(result)

    def test_bypass_on_refuses_restricted_repo(self) -> None:
        service = _make_service(self.tmpdir)
        service.approve('vendor', remote_url='git@example.com:vendor.git')
        weak = RuntimePosture(
            bypass_permissions=True,
            docker_mode_on=True,
            scanner_blocks_at_medium=True,
        )
        preflight = self._build_preflight(service, weak)
        task = SimpleNamespace(id='TASK-P2', summary='', description='', tags=[])
        captured: dict = {}

        def handler(failed_task, exc, prepared):
            captured['exc'] = exc

        result = preflight._enforce_restricted_execution_protocol(
            task,
            [SimpleNamespace(id='vendor')],
            failure_handler=handler,
        )

        self.assertFalse(result)
        self.assertIsInstance(captured['exc'], RestrictedModePostureViolation)
        self.assertEqual(captured['exc'].repository_ids, ['vendor'])
        self.assertTrue(any(
            'KATO_CLAUDE_BYPASS_PERMISSIONS' in v
            for v in captured['exc'].violations
        ))

    def test_docker_off_refuses_restricted_repo(self) -> None:
        service = _make_service(self.tmpdir)
        service.approve('vendor', remote_url='git@example.com:vendor.git')
        weak = RuntimePosture(
            bypass_permissions=False,
            docker_mode_on=False,
            scanner_blocks_at_medium=True,
        )
        preflight = self._build_preflight(service, weak)
        task = SimpleNamespace(id='TASK-P3', summary='', description='', tags=[])
        captured: dict = {}

        result = preflight._enforce_restricted_execution_protocol(
            task,
            [SimpleNamespace(id='vendor')],
            failure_handler=lambda t, e, p: captured.setdefault('exc', e),
        )

        self.assertFalse(result)
        self.assertTrue(any(
            'KATO_CLAUDE_DOCKER' in v
            for v in captured['exc'].violations
        ))

    def test_lenient_scanner_refuses_restricted_repo(self) -> None:
        service = _make_service(self.tmpdir)
        service.approve('vendor', remote_url='git@example.com:vendor.git')
        weak = RuntimePosture(
            bypass_permissions=False,
            docker_mode_on=True,
            scanner_blocks_at_medium=False,
        )
        preflight = self._build_preflight(service, weak)
        task = SimpleNamespace(id='TASK-P4', summary='', description='', tags=[])
        captured: dict = {}

        result = preflight._enforce_restricted_execution_protocol(
            task,
            [SimpleNamespace(id='vendor')],
            failure_handler=lambda t, e, p: captured.setdefault('exc', e),
        )

        self.assertFalse(result)
        self.assertTrue(any(
            'block_on_severity' in v
            for v in captured['exc'].violations
        ))

    def test_trusted_mode_repo_skips_posture_gate(self) -> None:
        # Operator has explicitly elevated this repo after review,
        # so the global posture is their problem — no extra
        # constraints applied.
        from kato_core_lib.data_layers.data.repository_approval import ApprovalMode

        service = _make_service(self.tmpdir)
        service.approve(
            'vendor', remote_url='git@example.com:vendor.git',
            mode=ApprovalMode.TRUSTED,
        )
        weak = RuntimePosture(
            bypass_permissions=True,
            docker_mode_on=False,
            scanner_blocks_at_medium=False,
        )
        preflight = self._build_preflight(service, weak)
        task = SimpleNamespace(id='TASK-P5', summary='', description='', tags=[])

        result = preflight._enforce_restricted_execution_protocol(
            task, [SimpleNamespace(id='vendor')],
        )

        self.assertTrue(result)

    def test_mixed_trusted_and_restricted_still_refuses(self) -> None:
        from kato_core_lib.data_layers.data.repository_approval import ApprovalMode

        service = _make_service(self.tmpdir)
        service.approve(
            'trusted-repo', remote_url='git@example.com:t.git',
            mode=ApprovalMode.TRUSTED,
        )
        service.approve('restricted-repo', remote_url='git@example.com:r.git')
        weak = RuntimePosture(
            bypass_permissions=True,
            docker_mode_on=True,
            scanner_blocks_at_medium=True,
        )
        preflight = self._build_preflight(service, weak)
        task = SimpleNamespace(id='TASK-P6', summary='', description='', tags=[])
        captured: dict = {}

        result = preflight._enforce_restricted_execution_protocol(
            task,
            [
                SimpleNamespace(id='trusted-repo'),
                SimpleNamespace(id='restricted-repo'),
            ],
            failure_handler=lambda t, e, p: captured.setdefault('exc', e),
        )

        self.assertFalse(result)
        # Only the restricted-mode id appears in the refusal.
        self.assertEqual(captured['exc'].repository_ids, ['restricted-repo'])

    def test_no_posture_supplier_skips_gate(self) -> None:
        # Legacy callers without a posture supplier still get the
        # approval gate — but the posture gate is a no-op.
        service = _make_service(self.tmpdir)
        service.approve('vendor', remote_url='git@example.com:vendor.git')
        preflight = self._build_preflight(service, posture=None)
        task = SimpleNamespace(id='TASK-P7', summary='', description='', tags=[])

        result = preflight._enforce_restricted_execution_protocol(
            task, [SimpleNamespace(id='vendor')],
        )

        self.assertTrue(result)

    def test_posture_supplier_exception_is_fail_open(self) -> None:
        # Approval gate already refused unapproved repos. The posture
        # gate is the second line — if reading posture itself fails,
        # we log and proceed rather than block on infrastructure.
        service = _make_service(self.tmpdir)
        service.approve('vendor', remote_url='git@example.com:vendor.git')

        def broken_supplier():
            raise RuntimeError('cannot read env')

        from kato_core_lib.data_layers.service.task_preflight_service import (
            TaskPreflightService,
        )
        preflight = TaskPreflightService.__new__(TaskPreflightService)
        preflight._repository_approval_service = service
        preflight._security_scanner_service = None
        preflight._workspace_provisioner = None
        preflight._runtime_posture_supplier = broken_supplier
        import logging
        preflight.logger = logging.getLogger('test-preflight-posture-broken')
        preflight._active_blocking_comment_log_state = {}
        task = SimpleNamespace(id='TASK-P8', summary='', description='', tags=[])

        result = preflight._enforce_restricted_execution_protocol(
            task, [SimpleNamespace(id='vendor')],
        )

        self.assertTrue(result)


if __name__ == '__main__':
    unittest.main()
