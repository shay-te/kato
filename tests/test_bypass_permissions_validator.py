"""Tests for the KATO_CLAUDE_DOCKER / KATO_CLAUDE_BYPASS_PERMISSIONS safety gate.

The validator handles two independent flags with one constraint between
them (bypass requires docker). Tests exercise each decision branch:

  * both off -> silent return (host execution)
  * bypass on, docker off -> refused at startup (§C)
  * docker on, native Windows -> refused (sandbox image is Linux)
  * (bypass OR docker), running as root -> refused
  * bypass on, no TTY -> refused
  * bypass on, TTY, both prompts yes -> allowed
  * bypass on, TTY, either prompt no -> refused

Most tests below set BOTH flags (DOCKER + BYPASS) so the §C
"bypass-requires-docker" refusal doesn't fire first and they actually
exercise the deeper check under test. The §C refusal itself has its
own test.
"""

from __future__ import annotations

import io
import os
import sys
import unittest
from unittest.mock import patch

from kato.validation.bypass_permissions_validator import (
    BYPASS_ENV_KEY,
    DOCKER_ENV_KEY,
    BypassPermissionsRefused,
    is_bypass_enabled,
    is_docker_mode_enabled,
    is_running_as_root,
    validate_bypass_permissions,
)


# Helper: env dict that sets BOTH flags. Every existing test that
# wants to reach a downstream check (root, Windows, non-TTY, prompts)
# uses this so the §C refusal (bypass-requires-docker) doesn't fire
# first. The §C refusal has its own dedicated test (see below).
_BYPASS_AND_DOCKER = {BYPASS_ENV_KEY: 'true', DOCKER_ENV_KEY: 'true'}


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

    def test_is_docker_mode_enabled_reads_env_dict(self) -> None:
        self.assertTrue(is_docker_mode_enabled({DOCKER_ENV_KEY: 'true'}))
        self.assertTrue(is_docker_mode_enabled({DOCKER_ENV_KEY: 'TRUE'}))
        self.assertTrue(is_docker_mode_enabled({DOCKER_ENV_KEY: 'yes'}))
        self.assertTrue(is_docker_mode_enabled({DOCKER_ENV_KEY: '1'}))
        self.assertFalse(is_docker_mode_enabled({DOCKER_ENV_KEY: 'false'}))
        self.assertFalse(is_docker_mode_enabled({DOCKER_ENV_KEY: ''}))
        self.assertFalse(is_docker_mode_enabled({}))

    def test_docker_and_bypass_are_independent_flags(self) -> None:
        """Each flag is read independently; setting one does not set the other."""
        self.assertFalse(is_docker_mode_enabled({BYPASS_ENV_KEY: 'true'}))
        self.assertFalse(is_bypass_enabled({DOCKER_ENV_KEY: 'true'}))


class BypassValidatorTests(unittest.TestCase):
    def test_bypass_off_returns_silently(self) -> None:
        stderr = io.StringIO()
        validate_bypass_permissions(env={}, stderr=stderr)
        self.assertEqual(stderr.getvalue(), '')

    def test_bypass_on_writes_banner_to_stderr(self) -> None:
        stderr = io.StringIO()
        with patch.object(os, 'geteuid', create=True, return_value=1000):
            validate_bypass_permissions(
                env=_BYPASS_AND_DOCKER,
                stderr=stderr,
                stdin=_FakeTTY(),
                yes_no_prompter=lambda *_: True,
            )
        self.assertIn('KATO_CLAUDE_BYPASS_PERMISSIONS=true', stderr.getvalue())
        self.assertIn('SECURITY.md', stderr.getvalue())

    def test_bypass_on_root_is_refused_unconditionally(self) -> None:
        """Bypass + root + docker -> root refusal (root check fires AFTER §C)."""
        stderr = io.StringIO()
        with patch.object(os, 'geteuid', create=True, return_value=0):
            with self.assertRaises(BypassPermissionsRefused) as cm:
                validate_bypass_permissions(env=_BYPASS_AND_DOCKER, stderr=stderr)
        self.assertIn('root', str(cm.exception))

    def test_docker_only_root_is_also_refused(self) -> None:
        """Root + docker (no bypass) -> root refusal too. Layer 2 gates on bypass OR docker."""
        stderr = io.StringIO()
        with patch.object(os, 'geteuid', create=True, return_value=0):
            with self.assertRaises(BypassPermissionsRefused) as cm:
                validate_bypass_permissions(
                    env={DOCKER_ENV_KEY: 'true'}, stderr=stderr,
                )
        self.assertIn('root', str(cm.exception))
        # Message names docker (not bypass) since only docker was set.
        self.assertIn(DOCKER_ENV_KEY, str(cm.exception))

    def test_docker_on_native_windows_is_refused_with_wsl2_redirect(self) -> None:
        """Docker mode on native Windows is refused (sandbox image is Linux).

        Refusal message names DOCKER_ENV_KEY explicitly so an operator
        who was running in host mode (no flags) and turned docker on
        understands docker is the incompatible flag, not their setup.
        """
        stderr = io.StringIO()
        with patch.object(sys, 'platform', 'win32'), \
                patch.object(os, 'geteuid', create=True, return_value=1000):
            with self.assertRaises(BypassPermissionsRefused) as cm:
                validate_bypass_permissions(
                    env={DOCKER_ENV_KEY: 'true'},
                    stderr=stderr,
                )
        msg = str(cm.exception)
        self.assertIn(DOCKER_ENV_KEY, msg)
        self.assertIn('Windows', msg)
        self.assertIn('WSL2', msg)

    def test_bypass_plus_docker_on_native_windows_hits_windows_refusal(self) -> None:
        """Bypass + docker on Windows -> Windows refusal (docker-gate fires before TTY/prompts)."""
        stderr = io.StringIO()
        with patch.object(sys, 'platform', 'win32'), \
                patch.object(os, 'geteuid', create=True, return_value=1000):
            with self.assertRaises(BypassPermissionsRefused) as cm:
                validate_bypass_permissions(
                    env=_BYPASS_AND_DOCKER,
                    stderr=stderr,
                    stdin=_FakeTTY(),
                    yes_no_prompter=lambda *_: True,
                )
        msg = str(cm.exception)
        # Windows refusal under docker-gate; must name docker, Windows, WSL2.
        self.assertIn(DOCKER_ENV_KEY, msg)
        self.assertIn('Windows', msg)
        self.assertIn('WSL2', msg)

    def test_bypass_on_non_windows_passes_platform_gate(self) -> None:
        """Linux / macOS / WSL2 (sys.platform != 'win32') proceed past the Windows gate."""
        stderr = io.StringIO()
        with patch.object(sys, 'platform', 'linux'), \
                patch.object(os, 'geteuid', create=True, return_value=1000):
            validate_bypass_permissions(
                env=_BYPASS_AND_DOCKER,
                stderr=stderr,
                stdin=_FakeTTY(),
                yes_no_prompter=lambda *_: True,
            )

    def test_bypass_on_non_interactive_is_refused(self) -> None:
        stderr = io.StringIO()
        with patch.object(os, 'geteuid', create=True, return_value=1000):
            with self.assertRaises(BypassPermissionsRefused) as cm:
                validate_bypass_permissions(
                    env=_BYPASS_AND_DOCKER,
                    stderr=stderr,
                    stdin=_FakeNonTTY(),
                )
        self.assertIn(BYPASS_ENV_KEY, str(cm.exception))

    def test_docker_only_non_interactive_is_allowed(self) -> None:
        """Docker-only mode (sandbox + prompts on) does NOT require a TTY.

        The double-prompt + non-TTY refusal exists because bypass turns
        off the per-tool prompts. Docker-only mode keeps prompts on,
        so non-interactive runners (CI / cron / systemd) can use it
        safely — the sandbox bounds blast radius without operator
        confirmation.
        """
        stderr = io.StringIO()
        with patch.object(os, 'geteuid', create=True, return_value=1000):
            # Should NOT raise even with a non-TTY stdin.
            validate_bypass_permissions(
                env={DOCKER_ENV_KEY: 'true'},
                stderr=stderr,
                stdin=_FakeNonTTY(),
            )

    def test_bypass_on_interactive_double_yes_continues(self) -> None:
        """Both prompts must answer yes; both questions must fire."""
        stderr = io.StringIO()
        prompts: list[str] = []

        def _yes(message, default):
            prompts.append(str(message))
            return True

        with patch.object(os, 'geteuid', create=True, return_value=1000):
            validate_bypass_permissions(
                env=_BYPASS_AND_DOCKER,
                stderr=stderr,
                stdin=_FakeTTY(),
                yes_no_prompter=_yes,
            )
        self.assertEqual(len(prompts), 2)
        self.assertIn('Are you sure', prompts[0])
        self.assertIn('Final confirmation', prompts[1])

    def test_bypass_on_interactive_first_no_is_refused(self) -> None:
        stderr = io.StringIO()
        with patch.object(os, 'geteuid', create=True, return_value=1000):
            with self.assertRaises(BypassPermissionsRefused) as cm:
                validate_bypass_permissions(
                    env=_BYPASS_AND_DOCKER,
                    stderr=stderr,
                    stdin=_FakeTTY(),
                    yes_no_prompter=lambda *_: False,
                )
        self.assertIn('declined', str(cm.exception).lower())

    def test_bypass_on_interactive_first_yes_second_no_is_refused(self) -> None:
        """A fat-fingered Enter on the first prompt must not slip through."""
        stderr = io.StringIO()
        answers = iter([True, False])

        def _prompter(_message, _default):
            return next(answers)

        with patch.object(os, 'geteuid', create=True, return_value=1000):
            with self.assertRaises(BypassPermissionsRefused) as cm:
                validate_bypass_permissions(
                    env=_BYPASS_AND_DOCKER,
                    stderr=stderr,
                    stdin=_FakeTTY(),
                    yes_no_prompter=_prompter,
                )
        self.assertIn('final confirmation', str(cm.exception).lower())

    # ----- the new §C refusal: bypass without docker -----

    def test_bypass_without_docker_is_refused_at_startup(self) -> None:
        """The motivating §C refusal: bypass requires docker.

        Without docker, bypass would mean per-tool prompts AND no
        sandbox — the agent runs every tool on the host without
        asking. Refused at startup with an operator-actionable
        message naming both flags and the social-vs-structural
        framing.
        """
        stderr = io.StringIO()
        with patch.object(os, 'geteuid', create=True, return_value=1000):
            with self.assertRaises(BypassPermissionsRefused) as cm:
                validate_bypass_permissions(
                    env={BYPASS_ENV_KEY: 'true'},
                    stderr=stderr,
                    stdin=_FakeTTY(),
                    yes_no_prompter=lambda *_: True,
                )
        msg = str(cm.exception)
        # Must name BOTH env vars so the operator knows the dependency.
        self.assertIn(BYPASS_ENV_KEY, msg)
        self.assertIn(DOCKER_ENV_KEY, msg)
        # Must name the social-vs-structural framing — the argument
        # for why bypass needs docker, not just that it does.
        self.assertIn('SOCIALLY', msg)
        self.assertIn('STRUCTURALLY', msg)
        # Must give the operator the two concrete fixes.
        self.assertIn('export KATO_CLAUDE_DOCKER=true', msg)
        self.assertIn('unset KATO_CLAUDE_BYPASS_PERMISSIONS', msg)
        # The bypass red banner still fires before the refusal so the
        # operator sees what was attempted before why it failed.
        self.assertIn('KATO_CLAUDE_BYPASS_PERMISSIONS=true', stderr.getvalue())

    # ----- four-mode coverage: every (docker, bypass) combination -----

    def test_mode_off_off_returns_silently(self) -> None:
        """Both flags off (default mode): host execution, prompts on."""
        stderr = io.StringIO()
        validate_bypass_permissions(env={}, stderr=stderr)
        # No banner, no refusal, nothing written to stderr.
        self.assertEqual(stderr.getvalue(), '')

    def test_mode_docker_on_bypass_off_proceeds_silently_on_tty_or_not(self) -> None:
        """Docker-only (NEW belt+suspenders mode): allowed silently, no banner, TTY not required."""
        for stdin in (_FakeTTY(), _FakeNonTTY()):
            with self.subTest(stdin=type(stdin).__name__):
                stderr = io.StringIO()
                with patch.object(os, 'geteuid', create=True, return_value=1000):
                    validate_bypass_permissions(
                        env={DOCKER_ENV_KEY: 'true'},
                        stderr=stderr,
                        stdin=stdin,
                    )
                # No bypass banner — docker-only mode doesn't trigger
                # the alarming red banner. The security-posture banner
                # in print_security_posture handles operator visibility
                # for this mode (Mode 2 banner).
                self.assertNotIn('!! KATO_CLAUDE_BYPASS_PERMISSIONS', stderr.getvalue())

    def test_mode_docker_off_bypass_on_is_refused(self) -> None:
        """The refused mode: bypass=true alone (no docker) -> §C refusal."""
        stderr = io.StringIO()
        with patch.object(os, 'geteuid', create=True, return_value=1000):
            with self.assertRaises(BypassPermissionsRefused):
                validate_bypass_permissions(
                    env={BYPASS_ENV_KEY: 'true'},
                    stderr=stderr,
                    stdin=_FakeTTY(),
                )

    def test_mode_docker_on_bypass_on_is_allowed_after_double_prompt(self) -> None:
        """The full bypass mode: docker + bypass + double-yes -> allowed."""
        stderr = io.StringIO()
        with patch.object(os, 'geteuid', create=True, return_value=1000):
            validate_bypass_permissions(
                env=_BYPASS_AND_DOCKER,
                stderr=stderr,
                stdin=_FakeTTY(),
                yes_no_prompter=lambda *_: True,
            )

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
