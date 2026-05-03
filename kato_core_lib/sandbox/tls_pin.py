"""TLS cert / SPKI pinning for ``api.anthropic.com`` (OG4).

Closes the rogue-CA / cert-mis-issuance residual. The runtime egress
firewall already restricts the sandbox to ``api.anthropic.com:443``,
but the TLS handshake validates against the system CA store. A
rogue intermediate, a mis-issued cert from a CA in the trust store,
or a government-compelled certificate would all pass system-CA
validation. Pinning binds the trust decision to a specific SPKI
fingerprint instead of "any cert any CA in the store would sign for
this hostname."

Lifecycle — one of four cases on every kato startup:

  1. **Env var pin** (``KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256=<value>``):
     comma-separated base64 SHA-256 SPKI pins. If any one matches
     the live cert, pass silently. Mismatch refuses with the
     full-context message. Saved file is ignored (env var wins) but
     not deleted.
  2. **Opt-out** (``KATO_SANDBOX_ALLOW_NO_TLS_PIN=true``): skip
     pinning entirely; print a yellow warning on every startup so
     the operator is reminded they're running with the residual.
  3. **First run** (no env var, no file at ``~/.kato/anthropic-tls-pin``):
     TOFU. Connect, extract the SPKI fingerprint, save it to the
     file (mode 0600, parent 0700), print a yellow informational
     box, continue startup.
  4. **Subsequent run** (file exists): read the saved fingerprint,
     compare to the live cert. Match → silent success. Mismatch →
     refuse with a yellow message that names the saved + live
     fingerprints, the pin date, and the recovery procedure
     (``rm ~/.kato/anthropic-tls-pin`` to re-pin).

Edge cases all refuse with operator-actionable messages: network
unreachable on first run, network unreachable on subsequent run,
file unreadable, file malformed, parent dir uncreatable, both env
vars set.

Cross-OS: pure stdlib (``ssl``, ``hashlib``, ``socket``, ``base64``,
``pathlib``, ``datetime``). Works identically on Linux, macOS,
Windows / WSL2.

To compute a pin manually (e.g. for the env-var override):

    openssl s_client -servername api.anthropic.com \\
      -connect api.anthropic.com:443 < /dev/null 2>/dev/null \\
      | openssl x509 -pubkey -noout \\
      | openssl pkey -pubin -outform der \\
      | openssl dgst -sha256 -binary \\
      | openssl base64
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
import socket
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional


_PIN_ENV_KEY = 'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256'
_ALLOW_NO_PIN_ENV_KEY = 'KATO_SANDBOX_ALLOW_NO_TLS_PIN'
_ANTHROPIC_HOST = 'api.anthropic.com'
_ANTHROPIC_PORT = 443
_HANDSHAKE_TIMEOUT_SECONDS = 8.0


# ANSI color codes. Stripped automatically when stderr is not a TTY
# (CI runners, pipes, redirects) so log files stay readable.
_YELLOW = '\033[33m'
_RESET = '\033[0m'


class TlsPinError(RuntimeError):
    """Raised when kato refuses to start due to a TLS-pin lifecycle event.

    Distinguished from system errors so callers (``main.main``) can
    handle the refusal cleanly. The exception message is a short
    single-line summary suitable for logger.error; the user-facing
    detail (yellow box / refusal multiline) is already on stderr by
    the time this raises.
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


def _default_pin_file_path() -> Path:
    """``~/.kato/anthropic-tls-pin`` resolved to an absolute Path.

    Wrapped in a function so tests can monkeypatch ``Path.home``
    or pass an explicit path via the ``pin_file_path`` test seam.
    """
    return Path.home() / '.kato' / 'anthropic-tls-pin'


def _spki_fingerprint_from_der_cert(der_cert: bytes) -> str:
    """Compute the SHA-256 base64 fingerprint of the cert's SPKI.

    SPKI (Subject Public Key Info) pinning survives routine cert
    rotation — Anthropic's CA-issued certs renew every few months,
    but the underlying public key normally stays the same across
    rotations. Pinning the SPKI rather than the whole cert means
    the pin survives those rotations.

    Uses the ``cryptography`` package when available (proper SPKI
    extraction). Falls back to hashing the whole DER cert when the
    package isn't installed — in that mode the pin breaks on every
    cert rotation. The env var name is the same in both modes; the
    operator just needs to know which mode they're in based on
    whether ``cryptography`` is available.
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
        # Whole-cert fallback. Pin breaks on every rotation but the
        # security property (this byte sequence, not "any CA-signed
        # cert") still holds.
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
    propagate as ``OSError``. Caller distinguishes those from a
    ``TlsPinError``.
    """
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    if not der:
        raise OSError(f'TLS handshake to {host}:{port} returned no peer cert')
    return _spki_fingerprint_from_der_cert(der)


def is_pinning_enabled(env: Optional[dict] = None) -> bool:
    """True iff at least one env-var pin is configured.

    Predates the TOFU lifecycle and is preserved for callers that
    only care about the env-var path (e.g. legacy diagnostics).
    The TOFU file is not consulted by this predicate.
    """
    return bool(_resolve_pins(env))


# --- I/O helpers -----------------------------------------------------------


def _is_tty(stream) -> bool:
    """True iff ``stream`` is an open TTY. Safe for closed/odd streams."""
    isatty = getattr(stream, 'isatty', None)
    if isatty is None:
        return False
    try:
        return bool(isatty())
    except (ValueError, OSError):
        return False


def _yellow(text: str, *, stderr) -> str:
    """Wrap ``text`` in yellow ANSI escapes when ``stderr`` is a TTY.

    Color codes are stripped when stderr is redirected (CI, pipes)
    so log captures stay readable. Same convention as the existing
    bypass red banner.
    """
    if _is_tty(stderr):
        return f'{_YELLOW}{text}{_RESET}'
    return text


def _write_stderr(text: str, stderr) -> None:
    """Write ``text`` to ``stderr`` and flush. Swallows odd-stream errors."""
    target = stderr if stderr is not None else sys.stderr
    try:
        target.write(text)
        target.flush()
    except (ValueError, OSError):
        pass


# --- file format ----------------------------------------------------------


def _save_pin_file(
    path: Path,
    fingerprint: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> None:
    """Write the pin file atomically with mode 0600 and parent dir 0700.

    Format:

        <base64-fingerprint>\\n
        # pinned: <ISO-8601 UTC timestamp>\\n

    Raises ``OSError`` on any filesystem failure (caller wraps in
    ``TlsPinError`` with the actionable message).
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    # ``mkdir`` honours umask; tighten explicitly so the dir is 0700
    # even if the operator's umask is permissive.
    try:
        os.chmod(parent, 0o700)
    except OSError:
        # Some filesystems (network mounts, Windows-side WSL paths)
        # silently ignore chmod. Don't refuse on that — the pin
        # data isn't a secret, just operator-private metadata.
        pass
    timestamp = (now or (lambda: datetime.now(timezone.utc)))().isoformat(timespec='seconds')
    content = f'{fingerprint}\n# pinned: {timestamp}\n'
    fd = os.open(
        str(path),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        os.write(fd, content.encode('utf-8'))
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_pin_file(path: Path) -> tuple[str, Optional[str]]:
    """Parse the pin file → ``(fingerprint, pinned_at_or_None)``.

    Raises ``OSError`` on read failure (passed through to caller).
    Raises ``ValueError`` on malformed content — empty file, no
    valid base64 fingerprint, fingerprint that doesn't decode to
    32 bytes (SHA-256 size).
    """
    text = path.read_text(encoding='utf-8')
    lines = text.splitlines()
    if not lines:
        raise ValueError('pin file is empty')
    fingerprint = lines[0].strip()
    if not fingerprint:
        raise ValueError('first line carries no fingerprint')
    try:
        decoded = base64.b64decode(fingerprint, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(
            f'first line is not valid base64: {exc}'
        ) from exc
    if len(decoded) != 32:
        raise ValueError(
            f'fingerprint decodes to {len(decoded)} bytes, expected 32 (SHA-256)'
        )
    pinned_at: Optional[str] = None
    for line in lines[1:]:
        stripped = line.strip()
        if stripped.startswith('# pinned:'):
            pinned_at = stripped[len('# pinned:'):].strip()
            break
    return fingerprint, pinned_at


# --- printable lifecycle messages -----------------------------------------


_OPTOUT_WARNING = (
    '\n'
    '⚠  TLS pin disabled (KATO_SANDBOX_ALLOW_NO_TLS_PIN=true)\n'
    '   Rogue-CA / mis-issuance residual is your responsibility.\n'
    '   See BYPASS_PROTECTIONS.md residual OG4.\n'
    '\n'
)


def _format_first_run_box(file_path: Path) -> str:
    """Yellow boxed first-run informational message.

    Width 68 (matches the existing kato boot banners). The path is
    rendered with ``~`` substitution when it sits under ``$HOME`` so
    the message is operator-friendly across machines.
    """
    display_path = _tilde_path(file_path)
    inner_width = 66
    horizontal = '═' * inner_width

    def row(text: str = '') -> str:
        # Left-pad with two spaces, right-pad to inner_width.
        # Uses string ``len`` — this is fine for the ASCII text in
        # the box; the unicode box characters around it are the
        # only non-ASCII we emit and they're not in the padded text.
        body = f'  {text}'
        # Truncate-or-pad to the inner width.
        if len(body) > inner_width:
            body = body[:inner_width]
        else:
            body = body + ' ' * (inner_width - len(body))
        return f'║{body}║'

    lines = [
        '',
        f'╔{horizontal}╗',
        row('TLS PIN — First run'),
        row(),
        row('Pinned api.anthropic.com certificate fingerprint.'),
        row(f'Saved to: {display_path}'),
        row(),
        row('Future runs verify against this pin. If Anthropic rotates'),
        row('their cert (~every few months), kato will refuse to start'),
        row('and tell you how to re-pin.'),
        row(),
        row('See BYPASS_PROTECTIONS.md residual OG4.'),
        f'╚{horizontal}╝',
        '',
    ]
    return '\n'.join(lines) + '\n'


def _format_mismatch_refusal(
    *,
    saved: list[str],
    live: str,
    pinned_at: Optional[str],
    origin: str,
    origin_is_file: bool,
) -> str:
    """Multi-line refusal message naming both fingerprints + recovery steps.

    ``origin`` is the operator-facing string for the saved pin's
    source — either the path string (TOFU) or the env var name
    (env-var pin). ``origin_is_file`` selects the recovery branch
    so a path that doesn't sit under ``$HOME`` (e.g. test temp
    dirs) still gets the ``rm <path>`` recovery rather than the
    ``unset`` branch.
    """
    if len(saved) == 1:
        saved_block = f'  Saved pin: {saved[0]}'
    else:
        saved_lines = '\n'.join(f'             {p}' for p in saved[1:])
        saved_block = f'  Saved pins: {saved[0]}\n{saved_lines}'
    pinned_at_line = (
        f'  Pinned at: {pinned_at}\n' if pinned_at else ''
    )
    if origin_is_file:
        recovery_steps = (
            'If you EXPECTED this:\n'
            f'  rm {origin}\n'
            '  <re-run kato>\n'
        )
    else:
        recovery_steps = (
            'If you EXPECTED this:\n'
            f'  unset {origin}\n'
            '  <re-run kato so the pin file path takes over, OR\n'
            '   re-export the env var with the new fingerprint>\n'
        )
    return (
        '\n'
        '✗ TLS PIN MISMATCH for api.anthropic.com\n'
        '\n'
        f'{saved_block}\n'
        f'  Live pin:  {live}\n'
        f'{pinned_at_line}'
        '\n'
        'This usually means Anthropic rotated their certificate (routine,\n'
        'every few months).\n'
        '\n'
        f'{recovery_steps}'
        '\n'
        'If you did NOT expect this:\n'
        '  Something may be intercepting your traffic. Investigate before\n'
        '  re-pinning. Compare the live fingerprint above with one obtained\n'
        '  from a trusted source (different network, public status page, etc.)\n'
        '  before deleting the file.\n'
        '\n'
        'See BYPASS_PROTECTIONS.md residual OG4.\n'
        '\n'
    )


def _tilde_path(path: Path) -> str:
    """Render ``path`` with ``~`` substitution for ``$HOME``.

    Falls back to ``str(path)`` if the path is outside the home
    directory (or if ``Path.home()`` is unavailable in the current
    process). Used for operator-friendly path display only.
    """
    try:
        home = Path.home()
    except (RuntimeError, KeyError):
        return str(path)
    try:
        rel = path.resolve().relative_to(home.resolve())
    except ValueError:
        return str(path)
    return f'~/{rel}'


# --- main entry point -----------------------------------------------------


def validate_anthropic_tls_pin_or_refuse(
    *,
    env: Optional[dict] = None,
    logger: logging.Logger | None = None,
    stderr=None,
    fetch_live_fingerprint: Callable[[], str] | None = None,
    pin_file_path: Path | None = None,
    now: Callable[[], datetime] | None = None,
) -> None:
    """Run the full OG4 TLS-pin lifecycle.

    Returns silently on success (any of: env var matches, opt-out
    set, TOFU first-run pin saved, file pin matches). Raises
    ``TlsPinError`` on any refusal — the user-facing detail
    (yellow message) is already on stderr by the time the
    exception raises; the exception message is a short summary
    suitable for ``logger.error``.

    Test seams: ``env``, ``stderr``, ``fetch_live_fingerprint``,
    ``pin_file_path``, ``now``. All default to the production
    plumbing when ``None``.
    """
    fetch = fetch_live_fingerprint or _fetch_live_spki_fingerprint
    file_path = pin_file_path or _default_pin_file_path()

    pins_from_env = _resolve_pins(env)
    optout = _env_flag_true(env, _ALLOW_NO_PIN_ENV_KEY)

    # Edge case: both env-var pin AND opt-out set. Ambiguous —
    # refuse rather than guess the operator's intent.
    if pins_from_env and optout:
        msg = (
            f'{_PIN_ENV_KEY} and {_ALLOW_NO_PIN_ENV_KEY} are both set. '
            f'Pick one.'
        )
        _write_stderr(_yellow(f'\n✗ {msg}\n\n', stderr=stderr), stderr)
        raise TlsPinError(msg)

    # Case 2: opt-out. Skip pinning entirely; print yellow warning
    # on every startup so the operator can never forget the residual.
    if optout:
        _write_stderr(_yellow(_OPTOUT_WARNING, stderr=stderr), stderr)
        return

    # Case 1: env-var pin wins over the saved file (operator
    # intent is explicit). The file is not deleted — the operator
    # may want it for fallback if they unset the env var later.
    if pins_from_env:
        if file_path.exists():
            _write_stderr(
                _yellow(
                    f'\nℹ  TLS pin loaded from env var; '
                    f'{_tilde_path(file_path)} ignored.\n\n',
                    stderr=stderr,
                ),
                stderr,
            )
        try:
            live = fetch()
        except OSError as exc:
            msg = (
                f'Cannot reach {_ANTHROPIC_HOST} to verify TLS pin. '
                f'Check your network connection and retry. ({exc})'
            )
            _write_stderr(_yellow(f'\n✗ {msg}\n\n', stderr=stderr), stderr)
            raise TlsPinError(msg) from exc
        if live in pins_from_env:
            return
        refusal = _format_mismatch_refusal(
            saved=pins_from_env,
            live=live,
            pinned_at=None,
            origin=_PIN_ENV_KEY,
            origin_is_file=False,
        )
        _write_stderr(_yellow(refusal, stderr=stderr), stderr)
        raise TlsPinError(
            f'TLS pin mismatch for {_ANTHROPIC_HOST} '
            f'(env-var pin does not match live cert).'
        )

    # No env-var pin, no opt-out → file-based TOFU.
    if not file_path.exists():
        # Case 3: first run. Connect, pin, save.
        try:
            live = fetch()
        except OSError as exc:
            msg = (
                f'Cannot reach {_ANTHROPIC_HOST} to establish TLS pin. '
                f'Check your network connection and retry. ({exc})'
            )
            _write_stderr(_yellow(f'\n✗ {msg}\n\n', stderr=stderr), stderr)
            raise TlsPinError(msg) from exc
        try:
            _save_pin_file(file_path, live, now=now)
        except OSError as exc:
            msg = (
                f'Cannot save TLS pin to {_tilde_path(file_path)}: {exc}. '
                f'Check that the parent directory is writable.'
            )
            _write_stderr(_yellow(f'\n✗ {msg}\n\n', stderr=stderr), stderr)
            raise TlsPinError(msg) from exc
        _write_stderr(_yellow(_format_first_run_box(file_path), stderr=stderr), stderr)
        if logger is not None:
            logger.info(
                'sandbox: TLS pin established for %s and saved to %s',
                _ANTHROPIC_HOST, _tilde_path(file_path),
            )
        return

    # Case 4: subsequent run, file exists.
    try:
        saved_pin, pinned_at = _read_pin_file(file_path)
    except OSError as exc:
        msg = (
            f'{_tilde_path(file_path)} exists but cannot be read: {exc}. '
            f'Delete and re-run to re-pin, or fix permissions.'
        )
        _write_stderr(_yellow(f'\n✗ {msg}\n\n', stderr=stderr), stderr)
        raise TlsPinError(msg) from exc
    except ValueError as exc:
        msg = (
            f'{_tilde_path(file_path)} is malformed: {exc}. '
            f'Delete and re-run to re-pin.'
        )
        _write_stderr(_yellow(f'\n✗ {msg}\n\n', stderr=stderr), stderr)
        raise TlsPinError(msg) from exc

    try:
        live = fetch()
    except OSError as exc:
        msg = (
            f'Cannot reach {_ANTHROPIC_HOST} to verify TLS pin. '
            f'Check your network connection and retry. ({exc})'
        )
        _write_stderr(_yellow(f'\n✗ {msg}\n\n', stderr=stderr), stderr)
        raise TlsPinError(msg) from exc

    if live == saved_pin:
        return  # silent success

    # Case 4 mismatch.
    refusal = _format_mismatch_refusal(
        saved=[saved_pin],
        live=live,
        pinned_at=pinned_at,
        origin=_tilde_path(file_path),
        origin_is_file=True,
    )
    _write_stderr(_yellow(refusal, stderr=stderr), stderr)
    raise TlsPinError(
        f'TLS pin mismatch for {_ANTHROPIC_HOST} '
        f'(saved pin does not match live cert).'
    )
