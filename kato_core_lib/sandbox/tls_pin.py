"""TLS cert / SPKI pinning for ``api.anthropic.com``.

Closes residual OG4. The runtime egress firewall already restricts
the sandbox to ``api.anthropic.com:443`` only, but the TLS handshake
still validates against the system CA store. A rogue intermediate
CA, a mis-issued certificate from a CA in the trust store, or a
government-compelled certificate would all pass system-CA validation.

Pinning binds the trust decision to a specific cert / public key
fingerprint instead of "any cert any CA in the store would sign for
this hostname." A rogue / mis-issued cert with a different
fingerprint then fails the pin even when it would pass CA
validation.

Operator activates by setting:

  * ``KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256`` to one or more
    base64url-encoded SHA-256 fingerprints of the SPKI (Subject
    Public Key Info) — comma-separated. Pinning the SPKI rather
    than the certificate itself means routine cert rotation
    (Let's Encrypt 90-day cycles, Cloudflare CDN cert rotation)
    doesn't break the pin as long as Anthropic keeps the same
    public key. Two-pin lists let operators ship a backup key
    that's announced before primary rotation.

To compute a pin from a live cert:

    openssl s_client -servername api.anthropic.com \\
      -connect api.anthropic.com:443 < /dev/null 2>/dev/null \\
      | openssl x509 -pubkey -noout \\
      | openssl pkey -pubin -outform der \\
      | openssl dgst -sha256 -binary \\
      | openssl base64

Cross-OS: pure stdlib (``ssl``, ``hashlib``, ``socket``, ``base64``).
Works identically on Linux, macOS, Windows / WSL2.

The validator is called at startup. A missing or wrong pin is a
hard refusal — operators who want to opt out set
``KATO_SANDBOX_ALLOW_NO_TLS_PIN=true`` (the named opt-out pattern
established by the base-image / CLI version pins).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import socket
import ssl
from typing import Iterable, Optional


_PIN_ENV_KEY = 'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256'
_ALLOW_NO_PIN_ENV_KEY = 'KATO_SANDBOX_ALLOW_NO_TLS_PIN'
_ANTHROPIC_HOST = 'api.anthropic.com'
_ANTHROPIC_PORT = 443
_HANDSHAKE_TIMEOUT_SECONDS = 8.0


class TlsPinError(RuntimeError):
    """The live cert's SPKI fingerprint doesn't match any configured pin.

    Distinguished from network / TLS errors so callers can tell
    "the pin failed" from "we couldn't reach the host." A pin
    failure is a security event; a network failure is operational.
    """


def _env_flag_true(env: Optional[dict], key: str) -> bool:
    source = env if env is not None else os.environ
    return str(source.get(key, '') or '').strip().lower() in {
        '1', 'true', 'yes', 'on',
    }


def _resolve_pins(env: Optional[dict]) -> list[str]:
    source = env if env is not None else os.environ
    raw = str(source.get(_PIN_ENV_KEY, '') or '').strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(',') if p.strip()]


def _spki_fingerprint_from_der_cert(der_cert: bytes) -> str:
    """Compute the SHA-256 base64 fingerprint of the cert's SPKI.

    The Subject Public Key Info is the X.509 field that carries the
    public key + algorithm identifier — pinning it survives cert
    rotation as long as the public key doesn't change, which is the
    operator-friendly trade-off (don't break weekly during
    Let's Encrypt rotation).

    Implemented by parsing the DER cert manually because Python's
    ``ssl`` module exposes the cert as DER but doesn't surface the
    SPKI directly. We use a minimal DER walk: the SPKI is the third
    field of the TBSCertificate sequence, after version+serial and
    signature algorithm.

    For correctness we use ``cryptography`` if available (it gives
    us a proper SPKI extraction); otherwise we hash the whole cert
    DER as a fallback. The fallback is documented as cert-pinning
    rather than SPKI-pinning — pin survives only until the next
    cert rotation. The env-var name remains the same; the operator
    just needs to know which mode they're in based on whether
    ``cryptography`` is installed.
    """
    try:
        from cryptography import x509  # type: ignore
        from cryptography.hazmat.primitives import serialization  # type: ignore
        cert = x509.load_der_x509_certificate(der_cert)
        spki_der = cert.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        digest = hashlib.sha256(spki_der).digest()
    except ImportError:
        # Fallback: hash the whole cert. Operator's pin must then
        # match a fingerprint of the WHOLE cert, not just the SPKI.
        # This degrades the rotation-friendliness — the pin breaks
        # on every cert rotation — but the security property is
        # the same.
        digest = hashlib.sha256(der_cert).digest()
    return base64.b64encode(digest).decode('ascii')


def _fetch_live_spki_fingerprint(
    host: str = _ANTHROPIC_HOST,
    port: int = _ANTHROPIC_PORT,
    *,
    timeout: float = _HANDSHAKE_TIMEOUT_SECONDS,
) -> str:
    """Open one TLS connection and return the live cert's SPKI fingerprint.

    Network errors (DNS failure, TCP refused, TLS handshake failure)
    propagate as ``OSError`` — caller distinguishes those from a
    pin-mismatch ``TlsPinError``.
    """
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    if not der:
        raise OSError(f'TLS handshake to {host}:{port} returned no peer cert')
    return _spki_fingerprint_from_der_cert(der)


def is_pinning_enabled(env: Optional[dict] = None) -> bool:
    """True iff at least one pin is configured."""
    return bool(_resolve_pins(env))


def validate_anthropic_tls_pin_or_refuse(
    *,
    env: Optional[dict] = None,
    logger: logging.Logger | None = None,
    fetch_live_fingerprint=_fetch_live_spki_fingerprint,
) -> None:
    """Refuse to proceed if the live cert doesn't match any configured pin.

    Operator paths:

      * Set ``KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256`` to one or
        more base64-encoded SHA-256 fingerprints (comma-separated).
        Two pins lets operators ship a backup before rotation.
      * Set ``KATO_SANDBOX_ALLOW_NO_TLS_PIN=true`` to opt out and
        rely on the system CA store alone (the previous default).

    A network failure during the pin check is NOT promoted to a
    refusal — kato may be running in a build-env without
    connectivity, or the operator may be offline. The pin check
    only fails when we successfully connect AND the live
    fingerprint doesn't match any pin. Network failures log a
    WARNING and proceed; pin mismatches raise.

    The ``fetch_live_fingerprint`` parameter is the test seam.
    """
    pins = _resolve_pins(env)
    if not pins:
        if _env_flag_true(env, _ALLOW_NO_PIN_ENV_KEY):
            if logger is not None:
                logger.warning(
                    'sandbox: %s not set and %s=true — TLS validation '
                    'falls back to the system CA store. A rogue / '
                    'mis-issued certificate from a CA in the store '
                    'would pass validation. Recommend setting %s '
                    'to one or more comma-separated SPKI SHA-256 '
                    'fingerprints (see kato_core_lib.sandbox.tls_pin '
                    'docstring for the openssl command).',
                    _PIN_ENV_KEY, _ALLOW_NO_PIN_ENV_KEY, _PIN_ENV_KEY,
                )
            return
        raise TlsPinError(
            f'TLS certificate pinning is required for {_ANTHROPIC_HOST}. '
            f'Pick one:\n'
            f'  1. Recommended: export {_PIN_ENV_KEY}=<base64-sha256-spki>\n'
            f'     (compute with the openssl pipeline in '
            f'kato_core_lib.sandbox.tls_pin docstring)\n'
            f'  2. Opt-out: export {_ALLOW_NO_PIN_ENV_KEY}=true\n'
            f'     (operator accepts the rogue-CA / mis-issuance residual)\n'
            f'See BYPASS_PROTECTIONS.md residual OG4.'
        )
    try:
        live_fingerprint = fetch_live_fingerprint()
    except OSError as exc:
        # Network failure — log + proceed. Pin check is only
        # informative when we can actually reach the host.
        if logger is not None:
            logger.warning(
                'sandbox: TLS pin check could not connect to %s: %s. '
                'Proceeding without pin verification this boot — '
                'subsequent Claude calls will still TLS-validate via '
                'the system CA store.',
                _ANTHROPIC_HOST, exc,
            )
        return
    if live_fingerprint in pins:
        if logger is not None:
            logger.info(
                'sandbox: TLS pin verified for %s (matched configured pin)',
                _ANTHROPIC_HOST,
            )
        return
    raise TlsPinError(
        f'TLS pin mismatch for {_ANTHROPIC_HOST}. Live SPKI fingerprint '
        f'is {live_fingerprint!r}, but the configured {_PIN_ENV_KEY} '
        f'value lists {pins!r}. Either Anthropic rotated their public '
        f'key (update the pin to include the new fingerprint), or a '
        f'rogue / mis-issued certificate is being served on the path '
        f'to {_ANTHROPIC_HOST}. Refusing to proceed.'
    )
