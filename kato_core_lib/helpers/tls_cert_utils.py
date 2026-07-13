"""Locally-trusted TLS certificate for the planning webserver.

A loopback address (``127.0.0.1``/``localhost``) can never get a
certificate from a real public CA — no CA will vouch for an address
that isn't a public, resolvable domain. So HTTPS for a local tool means
kato generates its own trust chain:

1. A local root CA (``ca-cert.pem`` / ``ca-key.pem``) — long-lived
   (~10 years), never itself presented over the wire.
2. A leaf/server certificate (``cert.pem`` / ``key.pem``) signed BY
   that CA — presented to browsers, short-lived (Chrome and other
   browsers reject a SERVER certificate valid for more than ~398 days,
   ``NET::ERR_CERT_VALIDITY_TOO_LONG``; that cap does not apply to a
   root CA sitting in a trust store, which is never itself a server
   cert), renewed automatically.

Same idea as the popular dev tool ``mkcert``: install the CA into the
OS trust store ONCE (see ``_install_ca_trust``) so every certificate
kato issues afterwards is trusted automatically — no more "your
connection is not private" click-through on every fresh cert. All
files persist under ``~/.kato/tls/`` (override via ``KATO_TLS_DIR``).

The CA private key is more sensitive than a plain leaf key: anyone who
reads it could mint a certificate for ANY domain that this machine's
browsers would trust silently. It's written with owner-only permissions
and never leaves ``~/.kato/tls/`` — same posture ``mkcert`` documents
for its own CA key.
"""
from __future__ import annotations

import datetime
import logging
import os
import subprocess
import sys
from pathlib import Path

from kato_core_lib.helpers.kato_paths_utils import kato_home_path

_ENV_DIR_KEY = 'KATO_TLS_DIR'
_CA_CERT_FILENAME = 'ca-cert.pem'
_CA_KEY_FILENAME = 'ca-key.pem'
_CERT_FILENAME = 'cert.pem'
_KEY_FILENAME = 'key.pem'
_TRUST_MARKER_FILENAME = '.ca-trusted-fingerprint'

# Server/leaf certs: capped at Chrome's max server-cert validity.
_LEAF_VALIDITY_DAYS = 397
_LEAF_RENEW_WITHIN_DAYS = 30
# The CA itself is never presented as a server cert, so it isn't
# subject to that cap — long-lived so the OS-trust step (which may
# need a user prompt) happens as rarely as possible.
_CA_VALIDITY_DAYS = 3650
_CA_RENEW_WITHIN_DAYS = 60


def _tls_dir() -> Path:
    return kato_home_path('tls', env_key=_ENV_DIR_KEY)


def ca_paths() -> tuple[Path, Path]:
    """``(ca_cert_path, ca_key_path)`` under the TLS dir."""
    tls_dir = _tls_dir()
    return tls_dir / _CA_CERT_FILENAME, tls_dir / _CA_KEY_FILENAME


def cert_paths() -> tuple[Path, Path]:
    """``(cert_path, key_path)`` for the LEAF/server cert (not
    guaranteed to exist). This is the pair passed to Flask's
    ``ssl_context`` and the one Electron pins by fingerprint."""
    tls_dir = _tls_dir()
    return tls_dir / _CERT_FILENAME, tls_dir / _KEY_FILENAME


def _trust_marker_path() -> Path:
    return _tls_dir() / _TRUST_MARKER_FILENAME


def _load_cert(cert_path: Path):
    from cryptography import x509
    return x509.load_pem_x509_certificate(cert_path.read_bytes())


def _not_near_expiry(cert, renew_within_days: int) -> bool:
    now = datetime.datetime.now(datetime.timezone.utc)
    return now < (cert.not_valid_after_utc - datetime.timedelta(days=renew_within_days))


def _leaf_signed_by(leaf_cert, ca_cert) -> bool:
    """True if ``leaf_cert``'s signature verifies against ``ca_cert``'s
    public key — i.e. the leaf was actually issued by THIS CA, not a
    prior (now-replaced) one."""
    try:
        ca_cert.public_key().verify(
            leaf_cert.signature,
            leaf_cert.tbs_certificate_bytes,
            _rsa_padding(),
            leaf_cert.signature_hash_algorithm,
        )
        return True
    except Exception:
        return False


def _rsa_padding():
    from cryptography.hazmat.primitives.asymmetric import padding
    return padding.PKCS1v15()


def _is_ca_usable(ca_cert_path: Path, ca_key_path: Path) -> bool:
    if not ca_cert_path.is_file() or not ca_key_path.is_file():
        return False
    try:
        cert = _load_cert(ca_cert_path)
    except Exception:
        return False
    return _not_near_expiry(cert, _CA_RENEW_WITHIN_DAYS)


def _is_leaf_usable(cert_path: Path, key_path: Path, ca_cert) -> bool:
    if not cert_path.is_file() or not key_path.is_file():
        return False
    try:
        cert = _load_cert(cert_path)
    except Exception:
        return False
    if not _not_near_expiry(cert, _LEAF_RENEW_WITHIN_DAYS):
        return False
    return _leaf_signed_by(cert, ca_cert)


def _write_key_and_cert(key_path: Path, cert_path: Path, key, cert) -> None:
    from cryptography.hazmat.primitives import serialization
    key_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass


def _generate_ca(ca_cert_path: Path, ca_key_path: Path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, 'kato local CA'),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'kato (local development)'),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(hours=1))
        .not_valid_after(now + datetime.timedelta(days=_CA_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    _write_key_and_cert(ca_key_path, ca_cert_path, key, cert)
    return cert, key


def _generate_leaf_cert(cert_path: Path, key_path: Path, ca_cert, ca_key):
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'kato-local')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(hours=1))
        .not_valid_after(now + datetime.timedelta(days=_LEAF_VALIDITY_DAYS))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName('localhost'),
                x509.IPAddress(ipaddress.ip_address('127.0.0.1')),
                x509.IPAddress(ipaddress.ip_address('::1')),
            ]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False,
                key_encipherment=True, data_encipherment=False,
                key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    _write_key_and_cert(key_path, cert_path, key, cert)


def _ensure_ca():
    """Return the (possibly freshly generated) CA cert object, key
    object, and whether it was just (re)generated."""
    ca_cert_path, ca_key_path = ca_paths()
    if _is_ca_usable(ca_cert_path, ca_key_path):
        cert = _load_cert(ca_cert_path)
        from cryptography.hazmat.primitives import serialization
        key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
        return cert, key, False
    cert, key = _generate_ca(ca_cert_path, ca_key_path)
    return cert, key, True


def ensure_local_tls_cert(logger=None, install_trust=False) -> tuple[str, str] | None:
    """Return ``(cert_path, key_path)`` (the LEAF/server cert) as
    strings, generating/renewing the CA and leaf as needed. Returns
    ``None`` (never raises) when ``cryptography`` is unavailable or the
    certs can't be written — the caller falls back to plain HTTP
    rather than failing to start.

    ``install_trust`` is OFF by default and must be requested
    explicitly by the real serving entrypoints (``kato_core_lib.main``,
    the dev ``kato_webserver.app.main``) — it triggers a BACKGROUND
    thread that may pop a native OS authorization prompt (Keychain, on
    macOS) to install the CA as a trusted root. Defaulting this to
    False means every other caller (tests, anything just wanting the
    cert files) can never accidentally trigger that OS-level side
    effect just by calling this function.
    """
    cert_path, key_path = cert_paths()
    try:
        ca_cert, ca_key, ca_is_new = _ensure_ca()
        if ca_is_new or not _is_leaf_usable(cert_path, key_path, ca_cert):
            _generate_leaf_cert(cert_path, key_path, ca_cert, ca_key)
    except Exception as exc:
        if logger is not None:
            logger.warning(
                'could not prepare a local TLS certificate (%s); '
                'falling back to plain HTTP', exc,
            )
        return None
    if install_trust:
        _install_ca_trust_in_background(logger)
    return str(cert_path), str(key_path)


def _install_ca_trust_in_background(logger=None) -> None:
    """Fire-and-forget: install the CA into the OS trust store unless
    it's already there. Runs in a daemon thread because the OS may show
    a graphical authorization prompt (Keychain access on macOS) — that
    must never block the webserver from starting and serving HTTPS
    (with the not-yet-OS-trusted cert, still safe — just still shows
    the browser warning until this completes)."""
    import threading
    threading.Thread(
        target=_install_ca_trust_if_needed,
        args=(logger,),
        name='kato-tls-ca-trust',
        daemon=True,
    ).start()


def _install_ca_trust_if_needed(logger=None) -> None:
    log = logger or logging.getLogger(__name__)
    ca_cert_path, _ca_key_path = ca_paths()
    try:
        fingerprint = _load_cert(ca_cert_path).fingerprint(_sha256())
    except Exception:
        return
    marker = _trust_marker_path()
    try:
        if marker.is_file() and marker.read_bytes() == fingerprint:
            return  # already installed this exact CA
    except OSError:
        pass
    installed = _install_ca_trust(str(ca_cert_path), log)
    if installed:
        try:
            marker.write_bytes(fingerprint)
        except OSError:
            pass


def _sha256():
    from cryptography.hazmat.primitives import hashes
    return hashes.SHA256()


def _install_ca_trust(ca_cert_path: str, logger) -> bool:
    """Best-effort, OS-specific install of the CA as a trusted root.
    Returns True only on confirmed success. Currently implemented for
    macOS (the login Keychain — no ``sudo``, but the OS may still show
    its own authorization prompt for a trust-settings change). Other
    platforms are a no-op: the certificate still works, the browser
    just keeps showing the one-time warning until trusted manually.
    """
    if sys.platform == 'darwin':
        return _install_ca_trust_macos(ca_cert_path, logger)
    logger.info(
        'kato local CA generated at %s but auto-trust is not implemented '
        'for this OS yet; the browser will show a one-time '
        'self-signed-certificate warning — trust it manually, or import '
        'this file as a trusted CA yourself.',
        ca_cert_path,
    )
    return False


def _install_ca_trust_macos(ca_cert_path: str, logger) -> bool:
    keychain = str(Path.home() / 'Library' / 'Keychains' / 'login.keychain-db')
    try:
        result = subprocess.run(
            [
                'security', 'add-trusted-cert', '-r', 'trustRoot',
                '-k', keychain, ca_cert_path,
            ],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as exc:
        logger.warning(
            'could not install kato\'s local CA into Keychain (%s); the '
            'browser will keep showing a one-time self-signed-certificate '
            'warning until this succeeds (kato retries on each restart)',
            exc,
        )
        return False
    if result.returncode != 0:
        logger.warning(
            'Keychain declined to trust kato\'s local CA (%s); the '
            'browser will keep showing a one-time self-signed-certificate '
            'warning until this succeeds (kato retries on each restart)',
            (result.stderr or '').strip() or f'exit code {result.returncode}',
        )
        return False
    logger.info(
        'installed kato\'s local CA into the login Keychain — HTTPS '
        'certificates are now trusted automatically, no browser warning',
    )
    return True
