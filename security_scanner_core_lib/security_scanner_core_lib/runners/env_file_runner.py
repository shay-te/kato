"""Validate ``.env`` files for committed real credentials.

The most common breach pattern is committing a real ``.env`` to a
remote-tracked branch. This runner walks the workspace for ``.env``
files (excluding the ``.example`` / ``.sample`` / ``.template``
variants which are intentional scaffolding), parses them, and flags
any value that looks like a real credential rather than a
placeholder.

Pure Python — no external dependencies. Always available.

Severity: ``CRITICAL`` for any real-looking value. The reasoning is
asymmetric — a placeholder mis-flagged is one warning the operator
clears; a real credential mis-cleared is a leaked secret. Default
to over-flagging, let the operator quiet false positives by adding
``# kato:placeholder`` next to the value.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from security_scanner_core_lib.security_scanner_core_lib.security_finding import (
    SecurityFinding,
    Severity,
)
from security_scanner_core_lib.security_scanner_core_lib.runners._helpers import (
    iter_workspace_files,
    workspace_relative,
)


# File-name patterns that are operator-credential containers.
_REAL_ENV_BASENAMES = frozenset({
    '.env',
    '.env.local',
    '.env.production',
    '.env.prod',
    '.env.staging',
    '.env.dev',
    '.env.development',
})

# File-name suffixes that are scaffolding — always exempt.
_SCAFFOLD_SUFFIXES = ('.example', '.sample', '.template', '.dist')

# A line looks placeholder-ish when it matches any of these patterns
# (case-insensitive). Anything else is treated as a real-looking
# value and flagged.
_PLACEHOLDER_PATTERNS = (
    re.compile(r'^\s*$'),                       # blank
    re.compile(r'^<.*>$'),                      # <your-key-here>
    re.compile(r'^\$\{.*\}$'),                  # ${ENV_VAR}
    re.compile(r'^(true|false|yes|no|on|off)$', re.IGNORECASE),
    re.compile(r'^(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?$', re.IGNORECASE),
    re.compile(r'^(debug|info|warn|error|trace)$', re.IGNORECASE),
    re.compile(r'^(http|https)://localhost', re.IGNORECASE),
    re.compile(r'^(replace[\W_]?me|change[\W_]?me|placeholder|todo|tbd|xxx+|none|null)$', re.IGNORECASE),
    re.compile(r'^your[\W_]', re.IGNORECASE),    # your-api-key, your_token
    re.compile(r'^example[\W_]', re.IGNORECASE),
    re.compile(r'^test[\W_]', re.IGNORECASE),
    # Common environment-stage names that operators set as plain
    # values (``STAGE=development`` etc). Long enough to not match
    # the bare-short check but always non-credential.
    re.compile(r'^(development|staging|production|prod|dev|local|qa|uat|sandbox)$', re.IGNORECASE),
    re.compile(r'^\d{1,5}$'),                    # bare ports / small numbers
    re.compile(r'^/[\w./-]*$'),                  # filesystem paths
)

# Default annotation that silences a line when added as a trailing comment.
# Operators can override this via the ``placeholder_annotation`` parameter.
_DEFAULT_PLACEHOLDER_ANNOTATION = 'security-scanner:placeholder'
_PLACEHOLDER_OVERRIDE = re.compile(
    r'#\s*security-scanner\s*:\s*placeholder', re.IGNORECASE
)


def _is_real_env(path: Path) -> bool:
    """True when ``path`` is an operator-credential ``.env`` file
    (not scaffolding).
    """
    name = path.name
    if name not in _REAL_ENV_BASENAMES:
        return False
    if any(name.endswith(suffix) for suffix in _SCAFFOLD_SUFFIXES):
        return False
    return True


def _value_looks_real(value: str) -> bool:
    """True when ``value`` doesn't match any placeholder pattern.

    Strips surrounding quotes first — operators often quote values
    in ``.env`` files, and the quote characters shouldn't decide
    whether something is real.
    """
    stripped = value.strip().strip('"').strip("'")
    if not stripped:
        return False
    if len(stripped) < 6:
        # Short values are almost always config flags / ports.
        return False
    return not any(p.match(stripped) for p in _PLACEHOLDER_PATTERNS)


def _parse_env_line(line: str) -> tuple[str, str, str] | None:
    """Parse a single ``.env`` line into ``(key, value, comment)``.

    Returns None for lines that aren't ``KEY=VALUE`` shape (comments,
    blanks, malformed). The trailing comment (anything after ``#``
    that's outside quotes) is returned separately so the override
    annotation can be checked without re-parsing.
    """
    line = line.rstrip('\n').rstrip('\r')
    if not line.strip() or line.lstrip().startswith('#'):
        return None
    if line.lstrip().startswith('export '):
        line = line.lstrip()[7:]
    if '=' not in line:
        return None
    key, _, rest = line.partition('=')
    key = key.strip()
    if not key or not key.replace('_', '').isalnum():
        return None
    # Pull off a trailing ``#`` comment that's not inside quotes.
    in_single = in_double = False
    comment = ''
    for i, ch in enumerate(rest):
        if ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '#' and not in_single and not in_double:
            comment = rest[i:].strip()
            rest = rest[:i]
            break
    return key, rest.strip(), comment


def run(
    workspace_path: str,
    logger: logging.Logger | None = None,
    *,
    placeholder_annotation: str | None = None,
) -> list[SecurityFinding]:
    """Walk ``workspace_path`` for ``.env`` files and flag real values.

    Returns the list of findings; never raises. Empty list when the
    workspace contains no real ``.env`` files (only scaffolding,
    or none at all).

    ``placeholder_annotation`` is the trailing-comment text that silences
    a line (default: ``security-scanner:placeholder``). Operators can pass
    a custom string to match a different annotation convention.
    """
    annotation = placeholder_annotation or _DEFAULT_PLACEHOLDER_ANNOTATION
    override_pattern = (
        _PLACEHOLDER_OVERRIDE
        if annotation == _DEFAULT_PLACEHOLDER_ANNOTATION
        else re.compile(
            r'#\s*' + re.escape(annotation.strip()) + r'\s*$',
            re.IGNORECASE,
        )
    )
    workspace = Path(workspace_path)
    findings: list[SecurityFinding] = []
    for entry in iter_workspace_files(workspace):
        if not _is_real_env(entry):
            continue
        try:
            text = entry.read_text(encoding='utf-8', errors='replace')
        except OSError as exc:
            if logger is not None:
                logger.warning('env-file runner: failed to read %s: %s', entry, exc)
            continue
        for line_no, raw in enumerate(text.splitlines(), start=1):
            parsed = _parse_env_line(raw)
            if parsed is None:
                continue
            key, value, comment = parsed
            if override_pattern.search(comment):
                continue
            if not _value_looks_real(value):
                continue
            findings.append(SecurityFinding(
                tool='env-file',
                severity=Severity.CRITICAL,
                rule_id='env-real-credential',
                message=(
                    f'{key} in {entry.name} looks like a real credential '
                    f'(not a placeholder). If this is intentional repo '
                    f'scaffolding, rename the file to {entry.name}.example '
                    f'or annotate the line with "# {annotation}".'
                ),
                path=workspace_relative(workspace, entry),
                line=line_no,
                metadata=(('key', key),),
            ))
    return findings
