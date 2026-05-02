"""Tests for the KATO_CLAUDE_BYPASS_PERMISSIONS safety gate.

Each test exercises one decision branch: bypass off, bypass + root,
bypass + non-interactive (refused), bypass + interactive + both
prompts say yes (allowed), bypass + interactive + first prompt says
no (refused), bypass + interactive + first yes / second no (refused).
"""

from __future__ import annotations

import io
import os
import sys
import unittest
from unittest.mock import patch

from kato.validation.bypass_permissions_validator import (
    BYPASS_ENV_KEY,
    BypassPermissionsRefused,
    is_bypass_enabled,
    is_running_as_root,
    validate_bypass_permissions,
)


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class _FakeNonTTY(io.StringIO):
    def isatty(self) -> bool:
        return False


class BypassDetectionTests(unittest.TestCase):
    def test_is_bypass_enabled_reads_env_dict(self) -> None:
        self.assertTrue(is_bypass_enabled({BYPASS_ENV_KEY: 'true'}))
        self.assertTrue(is_bypass_enabled({BYPASS_ENV_KEY: 'TRUE'}))
        self.assertTrue(is_bypass_enabled({BYPASS_ENV_KEY: 'yes'}))
        self.assertTrue(is_bypass_enabled({BYPASS_ENV_KEY: '1'}))
        self.assertFalse(is_bypass_enabled({BYPASS_ENV_KEY: 'false'}))
        self.assertFalse(is_bypass_enabled({BYPASS_ENV_KEY: ''}))
        self.assertFalse(is_bypass_enabled({}))


class BypassValidatorTests(unittest.TestCase):
    def test_bypass_off_returns_silently(self) -> None:
        stderr = io.StringIO()
        validate_bypass_permissions(env={}, stderr=stderr)
        self.assertEqual(stderr.getvalue(), '')

    def test_bypass_on_writes_banner_to_stderr(self) -> None:
        stderr = io.StringIO()
        env = {BYPASS_ENV_KEY: 'true'}
        with patch.object(os, 'geteuid', create=True, return_value=1000):
            validate_bypass_permissions(
                env=env,
                stderr=stderr,
                stdin=_FakeTTY(),
                yes_no_prompter=lambda *_: True,
            )
        self.assertIn('KATO_CLAUDE_BYPASS_PERMISSIONS=true', stderr.getvalue())
        self.assertIn('SECURITY.md', stderr.getvalue())

    def test_bypass_on_root_is_refused_unconditionally(self) -> None:
        env = {BYPASS_ENV_KEY: 'true'}
        stderr = io.StringIO()
        with patch.object(os, 'geteuid', create=True, return_value=0):
            with self.assertRaises(BypassPermissionsRefused) as cm:
                validate_bypass_permissions(env=env, stderr=stderr)
        self.assertIn('root', str(cm.exception))

    def test_bypass_on_native_windows_is_refused_with_wsl2_redirect(self) -> None:
        """Native Windows Python under bypass must be refused, not degraded.

        On native Windows: ``os.geteuid`` is absent (Layer 2 silently
        no-ops), the workspace path validator's POSIX path constants
        are wrong-shape for ``C:\\...`` host paths, ``fcntl.flock``
        is unavailable so the audit chain loses parallel-write
        protection, and the sandbox image is Linux-only. Rather than
        let any of those degrade silently, refuse with an actionable
        message pointing at WSL2.
        """
        env = {BYPASS_ENV_KEY: 'true'}
        stderr = io.StringIO()
        # The Windows refusal must fire BEFORE the root check, so the
        # geteuid mock here is just to make sure if the platform check
        # fails we'd hit a different (root) refusal instead of slipping
        # through entirely.
        with patch.object(sys, 'platform', 'win32'), \
                patch.object(os, 'geteuid', create=True, return_value=1000):
            with self.assertRaises(BypassPermissionsRefused) as cm:
                validate_bypass_permissions(
                    env=env,
                    stderr=stderr,
                    stdin=_FakeTTY(),
                    yes_no_prompter=lambda *_: True,
                )
        msg = str(cm.exception)
        # Must name what was rejected (the env var) AND the redirect
        # path (WSL2) so the operator knows what to do next.
        self.assertIn(BYPASS_ENV_KEY, msg)
        self.assertIn('Windows', msg)
        self.assertIn('WSL2', msg)

    def test_bypass_on_non_windows_passes_platform_gate(self) -> None:
        """Linux / macOS / WSL2 (where sys.platform != 'win32') proceed past the Windows gate.

        Sanity check that the Windows refusal isn't accidentally
        catching POSIX hosts. We mock platform to 'linux' to be
        explicit even though the test runner is already on a POSIX
        host — keeps the test robust to platform changes in the
        runner environment.
        """
        env = {BYPASS_ENV_KEY: 'true'}
        stderr = io.StringIO()
        with patch.object(sys, 'platform', 'linux'), \
                patch.object(os, 'geteuid', create=True, return_value=1000):
            # Should NOT raise — both prompts say yes.
            validate_bypass_permissions(
                env=env,
                stderr=stderr,
                stdin=_FakeTTY(),
                yes_no_prompter=lambda *_: True,
            )

    def test_bypass_on_non_interactive_is_refused(self) -> None:
        env = {BYPASS_ENV_KEY: 'true'}
        stderr = io.StringIO()
        with patch.object(os, 'geteuid', create=True, return_value=1000):
            with self.assertRaises(BypassPermissionsRefused) as cm:
                validate_bypass_permissions(
                    env=env,
                    stderr=stderr,
                    stdin=_FakeNonTTY(),
                )
        # The refusal message must name the env var so the operator
        # knows what to unset / how to make the run interactive.
        self.assertIn(BYPASS_ENV_KEY, str(cm.exception))

    def test_bypass_on_interactive_double_yes_continues(self) -> None:
        """Both prompts must answer yes; both questions must fire."""
        env = {BYPASS_ENV_KEY: 'true'}
        stderr = io.StringIO()
        prompts: list[str] = []

        def _yes(message, default):
            prompts.append(str(message))
            return True

        with patch.object(os, 'geteuid', create=True, return_value=1000):
            validate_bypass_permissions(
                env=env,
                stderr=stderr,
                stdin=_FakeTTY(),
                yes_no_prompter=_yes,
            )
        self.assertEqual(len(prompts), 2)
        self.assertIn('Are you sure', prompts[0])
        self.assertIn('Final confirmation', prompts[1])

    def test_bypass_on_interactive_first_no_is_refused(self) -> None:
        env = {BYPASS_ENV_KEY: 'true'}
        stderr = io.StringIO()
        with patch.object(os, 'geteuid', create=True, return_value=1000):
            with self.assertRaises(BypassPermissionsRefused) as cm:
                validate_bypass_permissions(
                    env=env,
                    stderr=stderr,
                    stdin=_FakeTTY(),
                    yes_no_prompter=lambda *_: False,
                )
        self.assertIn('declined', str(cm.exception).lower())

    def test_bypass_on_interactive_first_yes_second_no_is_refused(self) -> None:
        """A fat-fingered Enter on the first prompt must not slip through."""
        env = {BYPASS_ENV_KEY: 'true'}
        stderr = io.StringIO()
        answers = iter([True, False])

        def _prompter(_message, _default):
            return next(answers)

        with patch.object(os, 'geteuid', create=True, return_value=1000):
            with self.assertRaises(BypassPermissionsRefused) as cm:
                validate_bypass_permissions(
                    env=env,
                    stderr=stderr,
                    stdin=_FakeTTY(),
                    yes_no_prompter=_prompter,
                )
        self.assertIn('final confirmation', str(cm.exception).lower())

    def test_running_as_root_handles_missing_geteuid(self) -> None:
        with patch.object(os, 'geteuid', create=True, side_effect=AttributeError):
            # When the attr lookup fails entirely we fall through to False —
            # exercises the Windows code path where geteuid does not exist.
            saved = getattr(os, 'geteuid', None)
            try:
                if hasattr(os, 'geteuid'):
                    delattr(os, 'geteuid')
                self.assertFalse(is_running_as_root())
            finally:
                if saved is not None:
                    os.geteuid = saved  # type: ignore[attr-defined]


if __name__ == '__main__':
    unittest.main()
