"""Tests for the pre-spawn workspace credential scan.

Closes residual #18 (credential exfiltration) on the preventive
side: a credential committed to a file with an innocuous name (a
``config.yaml``, a migration, a README) is now caught before the
sandbox spawn that would feed it to the agent.

Two-layer contract being tested:

1. **File-name signal** still fires (existing behavior preserved).
2. **File-content signal** is the new behavior. Any file matched
   by ``kato.sandbox.credential_patterns`` blocks the spawn unless
   the existing ``KATO_SANDBOX_ALLOW_WORKSPACE_SECRETS=true``
   override is set.

Performance scope: the scan caps per-file reads at 1 MiB and skips
known-noisy trees (`.git`, `node_modules`, `venv`, `dist`, `build`,
…); these caps are tested explicitly so a future loosening would
register as a regression.
"""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from sandbox_core_lib.sandbox_core_lib.manager import (
    SandboxError,
    enforce_no_workspace_secrets,
    scan_workspace_for_secrets,
)


# Fake credential strings — same shape as kato/sandbox/credential_patterns.py
# fixtures, never resembling a real value.
_FAKE_AWS_KEY = 'AKIAEXAMPLEFAKE12345'
_FAKE_GITHUB_PAT = 'ghp_' + 'A' * 36
_FAKE_PEM_BLOCK = '-----BEGIN RSA PRIVATE KEY-----'


class WorkspaceContentScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, relative: str, content: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    # ---- file-name signal (regression coverage for existing behavior) ----

    def test_filename_signal_still_fires_for_dotenv(self) -> None:
        self._write('.env', 'DATABASE_URL=postgres://example')
        findings = scan_workspace_for_secrets(str(self.root))
        self.assertIn('.env', findings)

    # ---- content signal (the new layer this commit adds) ----

    def test_aws_key_in_innocuous_yaml_is_detected(self) -> None:
        self._write('config.yaml', f'aws_access_key_id: {_FAKE_AWS_KEY}\n')
        findings = scan_workspace_for_secrets(str(self.root))
        # Annotated as a content match so the operator can tell the
        # difference between a name-shaped match and a content match.
        self.assertTrue(
            any('config.yaml' in f and 'content:' in f for f in findings),
            f'expected content-match annotation in findings: {findings}',
        )
        self.assertTrue(
            any('aws_access_key_id' in f for f in findings),
            f'expected aws_access_key_id pattern name in findings: {findings}',
        )

    def test_pem_block_in_readme_is_detected(self) -> None:
        # A copy-pasted example PEM in a README is a real-world failure
        # mode: operator pastes a "for example" key that turns out to
        # be a real one.
        self._write('README.md', f'See the example:\n\n{_FAKE_PEM_BLOCK}\n')
        findings = scan_workspace_for_secrets(str(self.root))
        self.assertTrue(
            any('README.md' in f and 'pem_private_key_block' in f for f in findings),
            f'expected README.md pem match in findings: {findings}',
        )

    def test_clean_workspace_returns_empty(self) -> None:
        self._write('README.md', '# Project\n\nNothing sensitive here.\n')
        self._write('src/main.py', 'def main():\n    return 42\n')
        self.assertEqual(scan_workspace_for_secrets(str(self.root)), [])

    # ---- enforce_no_workspace_secrets refusal + override ----

    def test_enforce_refuses_when_credential_in_content(self) -> None:
        self._write('migrations/001.sql', f'-- key: {_FAKE_GITHUB_PAT}')
        with self.assertRaises(SandboxError) as cm:
            enforce_no_workspace_secrets(str(self.root))
        message = str(cm.exception)
        self.assertIn('migrations/001.sql', message)
        self.assertIn('github_pat_classic', message)

    def test_enforce_proceeds_when_override_env_is_set(self) -> None:
        self._write('migrations/001.sql', f'-- key: {_FAKE_GITHUB_PAT}')
        # No exception with the explicit override.
        enforce_no_workspace_secrets(
            str(self.root),
            env={'KATO_SANDBOX_ALLOW_WORKSPACE_SECRETS': 'true'},
        )

    # ---- scope of skip list (performance + noise control) ----

    def test_skips_dotgit_directory(self) -> None:
        # Real .git pack files often contain blob bytes that pattern-
        # match by accident. We deliberately don't scan inside .git.
        self._write('.git/config', f'token = {_FAKE_GITHUB_PAT}')
        findings = scan_workspace_for_secrets(str(self.root))
        self.assertEqual(findings, [])

    def test_skips_node_modules_directory(self) -> None:
        self._write('node_modules/foo/index.js', f'KEY = "{_FAKE_AWS_KEY}"')
        findings = scan_workspace_for_secrets(str(self.root))
        self.assertEqual(findings, [])

    def test_skips_venv_directory(self) -> None:
        self._write('venv/lib/site-packages/x.py', f'k = "{_FAKE_GITHUB_PAT}"')
        findings = scan_workspace_for_secrets(str(self.root))
        self.assertEqual(findings, [])

    def test_skips_files_larger_than_1mib(self) -> None:
        # Synthesise a >1 MiB file that contains a credential. The scan
        # caps per-file reads at 1 MiB to avoid pulling huge generated
        # blobs into memory; the trade-off is documented and tested.
        big_path = self.root / 'huge.bin'
        big_path.write_bytes(b'A' * (1_048_577) + _FAKE_AWS_KEY.encode())
        findings = scan_workspace_for_secrets(str(self.root))
        self.assertEqual(findings, [])

    # ---- audit-log noise control ----

    def test_logger_emits_warning_when_findings_present(self) -> None:
        self._write('config.yaml', f'aws: {_FAKE_AWS_KEY}\n')
        logger = logging.getLogger('test_workspace_credential_scan')
        with self.assertLogs(logger=logger, level='WARNING') as cm:
            scan_workspace_for_secrets(str(self.root), logger=logger)
        joined = ' '.join(cm.output)
        self.assertIn('config.yaml', joined)
        self.assertIn('aws_access_key_id', joined)

    def test_logger_silent_when_workspace_clean(self) -> None:
        self._write('main.py', 'print("hello")\n')
        logger = logging.getLogger('test_workspace_credential_scan')
        # No log records emitted at all on a clean workspace.
        with self.assertNoLogs(logger=logger, level='WARNING'):
            scan_workspace_for_secrets(str(self.root), logger=logger)


if __name__ == '__main__':
    unittest.main()
