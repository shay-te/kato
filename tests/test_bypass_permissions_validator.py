"""Tests for the KATO_CLAUDE_BYPASS_PERMISSIONS safety gate.

Each test exercises one decision branch: bypass off, bypass + root, bypass +
acknowledged, bypass + non-interactive (refused), bypass + interactive +
operator says yes, bypass + interactive + operator says no.
"""

from __future__ import annotations

import io
import os
import unittest
from unittest.mock import patch

from kato.validation.bypass_permissions_validator import (
    ACCEPT_ENV_KEY,
    BYPASS_ENV_KEY,
    BypassPermissionsRefused,
    is_accept_acknowledged,
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

    def test_is_accept_acknowledged_reads_env_dict(self) -> None:
        self.assertTrue(is_accept_acknowledged({ACCEPT_ENV_KEY: 'true'}))
        self.assertFalse(is_accept_acknowledged({ACCEPT_ENV_KEY: 'false'}))
        self.assertFalse(is_accept_acknowledged({}))


class BypassValidatorTests(unittest.TestCase):
    def test_bypass_off_returns_silently(self) -> None:
        stderr = io.StringIO()
        validate_bypass_permissions(env={}, stderr=stderr)
        self.assertEqual(stderr.getvalue(), '')

    def test_bypass_on_writes_banner_to_stderr(self) -> None:
        stderr = io.StringIO()
        env = {BYPASS_ENV_KEY: 'true', ACCEPT_ENV_KEY: 'true'}
        with patch.object(os, 'geteuid', create=True, return_value=1000):
            validate_bypass_permissions(env=env, stderr=stderr)
        self.assertIn('KATO_CLAUDE_BYPASS_PERMISSIONS=true', stderr.getvalue())
        self.assertIn('SECURITY.md', stderr.getvalue())

    def test_bypass_on_root_is_refused_unconditionally(self) -> None:
        env = {BYPASS_ENV_KEY: 'true', ACCEPT_ENV_KEY: 'true'}
        stderr = io.StringIO()
        with patch.object(os, 'geteuid', create=True, return_value=0):
            with self.assertRaises(BypassPermissionsRefused) as cm:
                validate_bypass_permissions(env=env, stderr=stderr)
        self.assertIn('root', str(cm.exception))

    def test_bypass_on_acknowledged_skips_prompt(self) -> None:
        env = {BYPASS_ENV_KEY: 'true', ACCEPT_ENV_KEY: 'true'}
        stderr = io.StringIO()

        def _should_not_be_called(*_a, **_k):
            raise AssertionError('prompter must not run when ACCEPT=true')

        with patch.object(os, 'geteuid', create=True, return_value=1000):
            validate_bypass_permissions(
                env=env,
                stderr=stderr,
                stdin=_FakeTTY(),
                yes_no_prompter=_should_not_be_called,
            )

    def test_bypass_on_non_interactive_without_accept_is_refused(self) -> None:
        env = {BYPASS_ENV_KEY: 'true'}
        stderr = io.StringIO()
        with patch.object(os, 'geteuid', create=True, return_value=1000):
            with self.assertRaises(BypassPermissionsRefused) as cm:
                validate_bypass_permissions(
                    env=env,
                    stderr=stderr,
                    stdin=_FakeNonTTY(),
                )
        self.assertIn(ACCEPT_ENV_KEY, str(cm.exception))

    def test_bypass_on_interactive_yes_continues(self) -> None:
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
        self.assertEqual(len(prompts), 1)
        self.assertIn('Are you sure', prompts[0])

    def test_bypass_on_interactive_no_is_refused(self) -> None:
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
