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

from kato.sandbox.manager import (
    SandboxError,
    _validate_base_image_pin_or_refuse,
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


if __name__ == '__main__':
    unittest.main()
