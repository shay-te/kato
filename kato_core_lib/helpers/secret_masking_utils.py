"""Mask secret-looking config values before logging or display.

Operator pain this addresses: boot logs, validation output, and
configuration dumps can echo tokens / passwords / API keys in
plaintext when the operator (or kato itself) prints a config
record. The sandbox-side credential scanner runs on AGENT output
post-spawn, not on kato's own pre-spawn config printouts — so
``BITBUCKET_APP_PASSWORD`` could land in plaintext in
``kato run`` stdout, journalctl, or a CI log.

This helper takes a (key, value) pair and returns either:
  - the value as-is when the key name doesn't suggest a secret
  - a masked form (``abc1****wxyz``) when it does

The classification rule is intentionally string-key-based, not
content-scan based. Content-scanning every config print would be
expensive AND wrong (a path string like
``/srv/secrets/config.toml`` shouldn't be masked just because it
contains the word "secret"). The key name is the operator's
declared intent — if they named a field ``api_key``, treat its
value as secret.
"""

from __future__ import annotations

# Lower-cased substrings that, when present in a key name, mark
# the value as secret-shaped. Each substring is generous —
# ``password`` matches ``user_password``, ``BITBUCKET_APP_PASSWORD``,
# and ``api_password_v2``. The string match is case-insensitive.
#
# DO NOT broaden ``key`` lightly: many legitimate field names
# contain ``key`` without holding a secret (``primary_key``,
# ``foreign_key``, ``map_key``). We restrict to compound forms
# that clearly read as credentials (``api_key``, ``private_key``,
# ``access_key``, ``secret_key``).
_SECRET_KEY_SUBSTRINGS = (
    'password',
    'passwd',
    'secret',
    'token',
    'bearer',
    'authorization',
    'api_key',
    'apikey',
    'api-key',
    'private_key',
    'privatekey',
    'private-key',
    'access_key',
    'accesskey',
    'access-key',
    'secret_key',
    'secretkey',
    'secret-key',
    'app_password',
    'app-password',
    'session_token',
    'session-token',
    'credential',
    'credentials',
)


# Show the first PREFIX chars + last SUFFIX chars; everything in
# between becomes ``****``. Keeps enough surface to confirm "yes
# this is the value I set" without exposing it. Short values
# (length <= PREFIX + SUFFIX + 4) get fully masked — there's not
# enough material to redact safely.
_DEFAULT_PREFIX_KEEP = 4
_DEFAULT_SUFFIX_KEEP = 4
_FULL_MASK = '****'


def is_secret_key(key: str) -> bool:
    """True when ``key`` looks like a name that holds a secret."""
    if not key:
        return False
    lowered = str(key).lower()
    return any(token in lowered for token in _SECRET_KEY_SUBSTRINGS)


def mask_value(
    value: object,
    *,
    prefix_keep: int = _DEFAULT_PREFIX_KEEP,
    suffix_keep: int = _DEFAULT_SUFFIX_KEEP,
) -> str:
    """Mask ``value`` for display. Returns a stringified, redacted form.

    Empty / None / falsy inputs return ``''`` so callers can
    distinguish "no value set" from "value redacted." Short
    non-empty values (under prefix+suffix+4 chars) are fully
    masked since there isn't enough material to safely show a
    prefix and suffix without exposing the whole secret.
    """
    text = '' if value is None else str(value)
    if not text:
        return ''
    if len(text) <= prefix_keep + suffix_keep + 4:
        # Too short to show any of the value safely.
        return _FULL_MASK
    return f'{text[:prefix_keep]}{_FULL_MASK}{text[-suffix_keep:]}'


def mask_for_display(
    key: str,
    value: object,
    *,
    prefix_keep: int = _DEFAULT_PREFIX_KEEP,
    suffix_keep: int = _DEFAULT_SUFFIX_KEEP,
) -> str:
    """Return a display-safe string for the (key, value) pair.

    The mask only triggers when the key name looks secret-shaped
    (``is_secret_key``). Otherwise the value is stringified and
    returned as-is. Used to wrap every config-line render that
    might print to stdout / a log file / a UI.

    Empty values return ``''`` even for secret-shaped keys —
    callers can distinguish "unset" from "redacted."
    """
    text = '' if value is None else str(value)
    if not is_secret_key(key):
        return text
    return mask_value(text, prefix_keep=prefix_keep, suffix_keep=suffix_keep)
