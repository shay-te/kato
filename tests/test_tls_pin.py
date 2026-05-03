"""Tests for TLS cert / SPKI pinning for ``api.anthropic.com`` (OG4).

Closes the rogue-CA / cert-mis-issuance residual. The runtime
egress firewall already restricts to ``api.anthropic.com:443``,
but the TLS handshake validates against the system CA store —
a compelled or compromised CA in that store could mint a valid
cert for the host. Pinning binds the trust decision to a specific
SPKI fingerprint instead.

Properties under test:

  * The function is strict-by-default — no pin AND no opt-out =
    refusal at startup, with both fix paths named.
  * ``KATO_SANDBOX_ALLOW_NO_TLS_PIN=true`` opts out, with a
    WARNING that names the residual the operator just accepted.
  * A configured pin that matches the live fingerprint passes
    silently with an INFO log.
  * A configured pin that doesn't match raises ``TlsPinError`` —
    distinguishable from network errors.
  * Network errors during the pin check are NOT promoted to
    refusals (operator may be offline, or kato may be in a
    build-env without connectivity).
  * Cross-OS: stdlib only, no platform-specific code.
"""

from __future__ import annotations

import logging
import unittest
from unittest.mock import patch

from kato_core_lib.sandbox.tls_pin import (
    TlsPinError,
    is_pinning_enabled,
    validate_anthropic_tls_pin_or_refuse,
)


# Two arbitrary base64-SHA256 strings shaped like real SPKI pins.
_FAKE_PRIMARY_PIN = 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='
_FAKE_BACKUP_PIN = 'BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB='
_FAKE_LIVE_FINGERPRINT = _FAKE_PRIMARY_PIN
_FAKE_WRONG_FINGERPRINT = 'CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC='


class IsPinningEnabledTests(unittest.TestCase):
    def test_no_env_means_disabled(self) -> None:
        self.assertFalse(is_pinning_enabled({}))

    def test_empty_env_means_disabled(self) -> None:
        self.assertFalse(
            is_pinning_enabled({'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': ''})
        )

    def test_single_pin_means_enabled(self) -> None:
        self.assertTrue(
            is_pinning_enabled({
                'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': _FAKE_PRIMARY_PIN,
            }),
        )


class StrictByDefaultRefusalTests(unittest.TestCase):
    def test_no_pin_no_optout_refuses(self) -> None:
        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(env={})
        message = str(cm.exception)
        # Both fix paths named so operator can pick.
        self.assertIn('KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256', message)
        self.assertIn('KATO_SANDBOX_ALLOW_NO_TLS_PIN', message)
        # Doc cross-reference for the residual.
        self.assertIn('OG4', message)

    def test_optout_allows_proceed_with_warning(self) -> None:
        logger = logging.getLogger('test_tls_pin')
        with self.assertLogs(logger=logger, level='WARNING') as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={'KATO_SANDBOX_ALLOW_NO_TLS_PIN': 'true'},
                logger=logger,
            )
        joined = ' '.join(cm.output)
        # Warning names the residual operator accepted.
        self.assertIn('rogue', joined.lower())
        self.assertIn('mis-issued', joined.lower())
        # And names the env var to flip to enable pinning later.
        self.assertIn('KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256', joined)


class PinMatchTests(unittest.TestCase):
    def test_matching_pin_passes_silently_with_info(self) -> None:
        logger = logging.getLogger('test_tls_pin')
        with self.assertLogs(logger=logger, level='INFO') as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': _FAKE_PRIMARY_PIN},
                logger=logger,
                fetch_live_fingerprint=lambda: _FAKE_LIVE_FINGERPRINT,
            )
        joined = ' '.join(cm.output)
        self.assertIn('verified', joined.lower())
        self.assertIn('api.anthropic.com', joined)

    def test_backup_pin_in_list_also_passes(self) -> None:
        # Operator lists primary + backup. Matching either is OK —
        # this is the rotation-friendly case.
        env = {
            'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': (
                f'{_FAKE_PRIMARY_PIN},{_FAKE_BACKUP_PIN}'
            ),
        }
        # No exception when the live fingerprint matches the BACKUP pin.
        validate_anthropic_tls_pin_or_refuse(
            env=env,
            fetch_live_fingerprint=lambda: _FAKE_BACKUP_PIN,
        )


class PinMismatchTests(unittest.TestCase):
    def test_mismatch_raises_tls_pin_error(self) -> None:
        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': _FAKE_PRIMARY_PIN},
                fetch_live_fingerprint=lambda: _FAKE_WRONG_FINGERPRINT,
            )
        message = str(cm.exception)
        # Operator-actionable message: the live fingerprint is named
        # so the operator can update the pin if Anthropic rotated.
        self.assertIn(_FAKE_WRONG_FINGERPRINT, message)
        self.assertIn(_FAKE_PRIMARY_PIN, message)
        # The two diagnostic interpretations are surfaced so the
        # operator knows which path to investigate first.
        self.assertIn('rotated', message.lower())
        self.assertIn('rogue', message.lower())

    def test_two_pins_neither_matching_raises(self) -> None:
        env = {
            'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': (
                f'{_FAKE_PRIMARY_PIN},{_FAKE_BACKUP_PIN}'
            ),
        }
        with self.assertRaises(TlsPinError):
            validate_anthropic_tls_pin_or_refuse(
                env=env,
                fetch_live_fingerprint=lambda: _FAKE_WRONG_FINGERPRINT,
            )


class NetworkFailureTests(unittest.TestCase):
    """Network errors are NOT promoted to refusals.

    Operator may be offline or in a build-env without connectivity;
    refusing to start kato in those cases would be a regression in
    operator UX. Pin verification is only informative when the
    handshake succeeds.
    """

    def test_network_failure_logs_warning_and_returns(self) -> None:
        def _raise_oserror() -> str:
            raise OSError('connection refused')

        logger = logging.getLogger('test_tls_pin')
        with self.assertLogs(logger=logger, level='WARNING') as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': _FAKE_PRIMARY_PIN},
                logger=logger,
                fetch_live_fingerprint=_raise_oserror,
            )
        joined = ' '.join(cm.output)
        # Warning names the failure mode + that we proceeded anyway.
        self.assertIn('connection refused', joined)
        self.assertIn('Proceeding', joined)


if __name__ == '__main__':
    unittest.main()
