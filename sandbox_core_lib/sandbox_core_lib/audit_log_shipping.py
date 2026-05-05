"""External audit-log shipping for the sandbox audit trail.

Closes residual OG2 (audit-log completeness). The local hash chain
already detects mid-chain tampering, but a tail-truncation followed
by fresh appends produces a valid-looking chain rooted at the new
tail — undetectable from local-file integrity alone. Shipping each
entry to an external sink as it's written gives operators a second
copy they can compare against the local file: missing entries on
disk that exist in the sink prove the tail was truncated.

Operator activates this by setting ``KATO_SANDBOX_AUDIT_SHIP_TARGET``
to a URL with one of the supported schemes:

  * ``https://example.com/path`` — POST each entry as ``application/json``.
    For S3 the recommended pattern is a presigned PUT URL behind a
    Lambda that writes to a bucket with Object Lock; the URL itself
    is the only secret kato needs.
  * ``file:///absolute/path/to/sink.log`` — append each entry as a
    JSON line to a local file outside the audit log's own directory.
    For operators who tail-ship the sink with their normal log
    pipeline (Vector, Fluent Bit, syslog forwarder).

Best-effort by default — a failed sink write logs a warning and the
spawn proceeds. Operators who want fail-closed shipping set
``KATO_SANDBOX_AUDIT_SHIP_REQUIRED=true``; a sink failure then
raises ``SandboxError`` and the spawn is refused.

Cross-OS: pure stdlib (``urllib.request`` for HTTPS, file IO for
``file://``). Works identically on Linux, macOS, and Windows / WSL2.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen


_SHIP_TARGET_ENV_KEY = 'KATO_SANDBOX_AUDIT_SHIP_TARGET'
_SHIP_REQUIRED_ENV_KEY = 'KATO_SANDBOX_AUDIT_SHIP_REQUIRED'
_SHIP_TIMEOUT_SECONDS = 5.0


class AuditShipError(RuntimeError):
    """Raised when shipping fails AND the operator opted into fail-closed.

    Distinguished from ``SandboxError`` so callers can tell "the spawn
    couldn't be audit-shipped" apart from "the sandbox itself
    refused" without parsing strings. The caller (``record_spawn``)
    promotes this to ``SandboxError`` when ``KATO_SANDBOX_AUDIT_SHIP_REQUIRED``
    is set.
    """


def _env_flag_true(env: Optional[dict], key: str) -> bool:
    source = env if env is not None else os.environ
    return str(source.get(key, '') or '').strip().lower() in {
        '1', 'true', 'yes', 'on',
    }


def _resolve_target(env: Optional[dict]) -> str:
    source = env if env is not None else os.environ
    return str(source.get(_SHIP_TARGET_ENV_KEY, '') or '').strip()


def _ship_to_https(target: str, entry: dict) -> None:
    """POST one entry as JSON to an HTTPS endpoint.

    Uses the system CA store; for endpoints behind a private CA the
    operator points the URL at a forwarder that re-emits to the
    final sink. We deliberately do NOT accept ``http://`` — audit
    entries can carry workspace paths and task IDs that the
    operator may consider sensitive, so plaintext shipping is
    refused.
    """
    if not target.startswith('https://'):
        raise AuditShipError(
            f'audit-ship target {target!r} is not https:// — '
            f'plaintext shipping is refused for sensitivity reasons. '
            f'Use https:// or file:// instead.'
        )
    body = json.dumps(entry, ensure_ascii=False).encode('utf-8')
    request = Request(
        target,
        data=body,
        method='POST',
        headers={
            'Content-Type': 'application/json',
            'User-Agent': 'kato-sandbox-audit-ship/1.0',
        },
    )
    context = ssl.create_default_context()
    try:
        with urlopen(request, timeout=_SHIP_TIMEOUT_SECONDS, context=context) as response:
            status = response.getcode()
            if not 200 <= status < 300:
                raise AuditShipError(
                    f'audit-ship POST to {target} returned status {status}'
                )
    except (OSError, ValueError) as exc:
        # ValueError covers urllib's URL-malformed errors; OSError
        # covers network / TLS failures.
        raise AuditShipError(
            f'audit-ship POST to {target} failed: {exc}'
        ) from exc


def _ship_to_file(target: str, entry: dict) -> None:
    """Append one entry as a JSON line to a local file.

    The file is opened with ``O_APPEND`` so concurrent shippers (a
    parallel kato spawn writing the same sink) can't lose entries
    to interleaving. We DO NOT chmod the file — the sink lives
    under operator control and the operator chose its location +
    permissions. Cross-OS: ``O_APPEND`` is atomic on Linux/macOS
    POSIX file systems and on NTFS via the Windows port.
    """
    parsed = urlparse(target)
    sink_path = parsed.path
    if not sink_path:
        raise AuditShipError(
            f'audit-ship target {target!r} has no path component '
            f'(expected ``file:///absolute/path``)'
        )
    sink = Path(sink_path)
    try:
        sink.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AuditShipError(
            f'audit-ship cannot create parent of {sink}: {exc}'
        ) from exc
    line = (json.dumps(entry, ensure_ascii=False) + '\n').encode('utf-8')
    try:
        fd = os.open(
            str(sink),
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o600,
        )
    except OSError as exc:
        raise AuditShipError(
            f'audit-ship cannot open sink {sink}: {exc}'
        ) from exc
    try:
        os.write(fd, line)
        os.fsync(fd)
    except OSError as exc:
        raise AuditShipError(
            f'audit-ship write to {sink} failed: {exc}'
        ) from exc
    finally:
        os.close(fd)


def is_shipping_enabled(env: Optional[dict] = None) -> bool:
    """True iff the operator set the target env var to a non-empty value."""
    return bool(_resolve_target(env))


def ship_audit_entry(
    entry: dict,
    *,
    env: Optional[dict] = None,
    logger: logging.Logger | None = None,
) -> None:
    """Ship one audit entry to the configured external sink.

    No-op when the operator hasn't set ``KATO_SANDBOX_AUDIT_SHIP_TARGET``.
    Best-effort by default: a failed ship logs a warning and returns
    silently. Set ``KATO_SANDBOX_AUDIT_SHIP_REQUIRED=true`` to
    promote ship failures into ``AuditShipError``; the caller
    (``record_spawn``) re-raises that as ``SandboxError`` and the
    spawn is refused.

    The two supported target schemes are dispatched here so the
    public API has one entry point. Adding a new scheme means
    adding a branch + a unit test that covers it.
    """
    target = _resolve_target(env)
    if not target:
        return
    try:
        if target.startswith('https://'):
            _ship_to_https(target, entry)
        elif target.startswith('file://'):
            _ship_to_file(target, entry)
        else:
            raise AuditShipError(
                f'audit-ship target {target!r} uses an unsupported scheme. '
                f'Supported: https://, file://. (http:// is refused for '
                f'sensitivity reasons; add a forwarder if you need it.)'
            )
    except AuditShipError as exc:
        if _env_flag_true(env, _SHIP_REQUIRED_ENV_KEY):
            # Caller (``record_spawn``) catches and promotes to
            # SandboxError so the spawn is refused.
            raise
        if logger is not None:
            logger.warning(
                'audit-ship to %s failed: %s — entry is on the local '
                'audit log but NOT in the external sink. Set %s=true '
                'to fail-close on this.',
                target, exc, _SHIP_REQUIRED_ENV_KEY,
            )
