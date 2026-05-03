"""Tests for the mandatory base-image digest pin.

Closes residual #17 on the build-time supply-chain side by
changing a default. The previous behavior allowed building with the
moving ``node:22-bookworm-slim`` tag, leaving the build-time chain
unbounded against a hostile registry / DNS hijack / corporate proxy
during the next build. The new strict-by-default refuses unless the
operator either:

  1. Pins a digest via ``KATO_SANDBOX_BASE_IMAGE=node:...@sha256:<digest>``
     (recommended), or
  2. Opts out via ``KATO_SANDBOX_ALLOW_FLOATING_BASE_IMAGE=true``
     (operator accepts the residual in writing).

Half-pinned values (a tag without ``@sha256:``) are also refused,
because half-pinning provides no protection but gives the operator
a false sense of security.
"""

from __future__ import annotations

import logging
import unittest

from unittest.mock import patch

from kato_core_lib.sandbox.manager import (
    SandboxError,
    _validate_base_image_pin_or_refuse,
    _validate_claude_cli_version_pin_or_refuse,
    build_image,
)


class BaseImageDigestPinValidationTests(unittest.TestCase):
    def test_refuses_when_no_env_var_set(self) -> None:
        """Strict default: empty env → refusal with both fix paths named."""
        with self.assertRaises(SandboxError) as cm:
            _validate_base_image_pin_or_refuse(env={})
        message = str(cm.exception)
        # Both opt-paths named so the operator can pick.
        self.assertIn('KATO_SANDBOX_BASE_IMAGE', message)
        self.assertIn('KATO_SANDBOX_ALLOW_FLOATING_BASE_IMAGE', message)
        # Reason for the refusal is named so the operator understands
        # why the previous default changed.
        self.assertIn('supply chain', message.lower())
        # Doc cross-reference so a future reader can find the residual.
        self.assertIn('BYPASS_PROTECTIONS.md', message)

    def test_refuses_when_base_image_lacks_digest(self) -> None:
        """Half-pinned (tag-only) value is worse than nothing — refused."""
        with self.assertRaises(SandboxError) as cm:
            _validate_base_image_pin_or_refuse(
                env={'KATO_SANDBOX_BASE_IMAGE': 'node:22-bookworm-slim'},
            )
        message = str(cm.exception)
        self.assertIn('does not include a digest pin', message)
        self.assertIn('@sha256:', message)
        # Names the value the operator passed so they see the typo /
        # missed digest at a glance.
        self.assertIn('node:22-bookworm-slim', message)

    def test_accepts_digest_pinned_value(self) -> None:
        """Digest-pinned value passes silently."""
        digest = 'a' * 64
        # No exception.
        _validate_base_image_pin_or_refuse(
            env={
                'KATO_SANDBOX_BASE_IMAGE':
                    f'node:22-bookworm-slim@sha256:{digest}',
            },
        )

    def test_accepts_floating_with_explicit_optout(self) -> None:
        """Operator-discretionary opt-out via the explicit env var."""
        # No exception.
        _validate_base_image_pin_or_refuse(
            env={'KATO_SANDBOX_ALLOW_FLOATING_BASE_IMAGE': 'true'},
        )

    def test_optout_supports_common_truthy_values(self) -> None:
        for truthy in ('true', 'TRUE', 'yes', '1', 'on'):
            try:
                _validate_base_image_pin_or_refuse(
                    env={'KATO_SANDBOX_ALLOW_FLOATING_BASE_IMAGE': truthy},
                )
            except SandboxError:
                self.fail(f'opt-out should accept {truthy!r}')

    def test_optout_does_not_match_falsy_or_partial_strings(self) -> None:
        # Defense against accidental opt-out via a stale / weird env value.
        for falsy in ('false', '0', '', 'no', 'truthy', 'yes please'):
            with self.assertRaises(
                SandboxError,
                msg=f'opt-out must NOT match {falsy!r}',
            ):
                _validate_base_image_pin_or_refuse(
                    env={'KATO_SANDBOX_ALLOW_FLOATING_BASE_IMAGE': falsy},
                )

    def test_optout_logs_warning_naming_the_residual(self) -> None:
        """When the opt-out path fires, log loudly so the operator sees it."""
        logger = logging.getLogger('test_base_image_digest_pin')
        with self.assertLogs(logger=logger, level='WARNING') as cm:
            _validate_base_image_pin_or_refuse(
                env={'KATO_SANDBOX_ALLOW_FLOATING_BASE_IMAGE': 'true'},
                logger=logger,
            )
        joined = ' '.join(cm.output)
        # The warning names the residual so it's not silent.
        self.assertIn('FLOATING', joined)
        self.assertIn('substitute', joined)

    def test_pinned_path_logs_info_naming_the_pin(self) -> None:
        """Successful pin logs at INFO so the operator sees what's used."""
        logger = logging.getLogger('test_base_image_digest_pin')
        digest = 'b' * 64
        pinned = f'node:22-bookworm-slim@sha256:{digest}'
        with self.assertLogs(logger=logger, level='INFO') as cm:
            _validate_base_image_pin_or_refuse(
                env={'KATO_SANDBOX_BASE_IMAGE': pinned},
                logger=logger,
            )
        joined = ' '.join(cm.output)
        self.assertIn(pinned, joined)
        self.assertIn('digest-pinned', joined)


class BuildImageInvokesValidatorTests(unittest.TestCase):
    """Lock the integration: ``build_image`` MUST call the validator.

    Without this assertion, a refactor that removes the validator call
    from ``build_image`` would leave every unit test in
    ``BaseImageDigestPinValidationTests`` green but ship a regression
    that bypasses the strict-by-default refusal entirely. This is the
    "all the unit tests pass but the call site was deleted" gap.
    """

    def test_build_image_refuses_when_validator_would_refuse(self) -> None:
        """Strict-default refusal flows through build_image too."""
        # Empty env triggers the refusal path the validator unit-tests
        # cover. If build_image didn't call the validator, the refusal
        # wouldn't fire and docker build would proceed unconstrained.
        with self.assertRaises(SandboxError):
            build_image(env={})

    def test_build_image_passes_through_to_docker_when_validator_accepts(self) -> None:
        """When the operator opts out of BOTH supply-chain pins, build_image proceeds.

        We mock subprocess so the test doesn't actually run docker; the
        assertion is that the validators returned silently and the
        docker invocation was reached. Both opt-outs must be set
        because both validators are now strict-by-default.
        """
        with patch(
            'kato_core_lib.sandbox.manager.subprocess.run',
            return_value=type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})(),
        ) as mock_run:
            build_image(env={
                'KATO_SANDBOX_ALLOW_FLOATING_BASE_IMAGE': 'true',
                'KATO_SANDBOX_ALLOW_FLOATING_CLAUDE_CLI': 'true',
            })

        # docker build was invoked at least once — i.e. both validators
        # returned silently.
        self.assertTrue(mock_run.called)
        # First arg is the docker build argv.
        invoked_argv = mock_run.call_args.args[0]
        self.assertEqual(invoked_argv[:2], ['docker', 'build'])

    def test_build_image_refuses_when_only_base_image_opt_out_is_set(self) -> None:
        """Both pins are independently strict — opting out of one isn't enough."""
        with self.assertRaises(SandboxError) as cm:
            build_image(env={'KATO_SANDBOX_ALLOW_FLOATING_BASE_IMAGE': 'true'})
        # The CLI version validator should be the one refusing here.
        message = str(cm.exception)
        self.assertIn('Claude CLI version', message)


class ClaudeCliVersionPinValidationTests(unittest.TestCase):
    """Mirror of BaseImageDigestPinValidationTests for the npm-side pin.

    Closes the npm-side slice of build-time supply chain (residual
    #17) by changing the default. The previous behavior installed
    ``@anthropic-ai/claude-code@latest`` — a malicious release pushed
    between operator builds would land in the next built image.
    """

    def test_refuses_when_no_env_var_set(self) -> None:
        with self.assertRaises(SandboxError) as cm:
            _validate_claude_cli_version_pin_or_refuse(env={})
        message = str(cm.exception)
        # Both opt-paths named so the operator can pick.
        self.assertIn('KATO_SANDBOX_CLAUDE_CLI_VERSION', message)
        self.assertIn('KATO_SANDBOX_ALLOW_FLOATING_CLAUDE_CLI', message)
        # Reason for the refusal is named so the operator understands
        # why the previous default changed.
        self.assertIn('supply chain', message.lower())
        # Doc cross-reference.
        self.assertIn('BYPASS_PROTECTIONS.md', message)

    def test_accepts_pinned_version(self) -> None:
        # No exception.
        _validate_claude_cli_version_pin_or_refuse(
            env={'KATO_SANDBOX_CLAUDE_CLI_VERSION': '2.1.5'},
        )

    def test_accepts_floating_with_explicit_optout(self) -> None:
        # No exception.
        _validate_claude_cli_version_pin_or_refuse(
            env={'KATO_SANDBOX_ALLOW_FLOATING_CLAUDE_CLI': 'true'},
        )

    def test_optout_supports_common_truthy_values(self) -> None:
        for truthy in ('true', 'TRUE', 'yes', '1', 'on'):
            try:
                _validate_claude_cli_version_pin_or_refuse(
                    env={'KATO_SANDBOX_ALLOW_FLOATING_CLAUDE_CLI': truthy},
                )
            except SandboxError:
                self.fail(f'opt-out should accept {truthy!r}')

    def test_optout_does_not_match_falsy_or_partial_strings(self) -> None:
        for falsy in ('false', '0', '', 'no', 'truthy', 'yes please'):
            with self.assertRaises(
                SandboxError,
                msg=f'opt-out must NOT match {falsy!r}',
            ):
                _validate_claude_cli_version_pin_or_refuse(
                    env={'KATO_SANDBOX_ALLOW_FLOATING_CLAUDE_CLI': falsy},
                )

    def test_optout_logs_warning_naming_the_residual(self) -> None:
        logger = logging.getLogger('test_claude_cli_version_pin')
        with self.assertLogs(logger=logger, level='WARNING') as cm:
            _validate_claude_cli_version_pin_or_refuse(
                env={'KATO_SANDBOX_ALLOW_FLOATING_CLAUDE_CLI': 'true'},
                logger=logger,
            )
        joined = ' '.join(cm.output)
        self.assertIn('FLOATING', joined)
        self.assertIn('npm', joined.lower())

    def test_pinned_path_logs_info_naming_the_pin(self) -> None:
        logger = logging.getLogger('test_claude_cli_version_pin')
        with self.assertLogs(logger=logger, level='INFO') as cm:
            _validate_claude_cli_version_pin_or_refuse(
                env={'KATO_SANDBOX_CLAUDE_CLI_VERSION': '2.1.5'},
                logger=logger,
            )
        joined = ' '.join(cm.output)
        self.assertIn('2.1.5', joined)
        self.assertIn('pinned', joined)


if __name__ == '__main__':
    unittest.main()
