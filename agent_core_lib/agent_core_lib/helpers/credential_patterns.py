"""High-confidence credential and operator-phishing pattern detector.

Agent backends and sandbox/workspace guards share these patterns so the
same named signals are used before a spawn and after an agent response.
The detector is deliberately narrow: each credential regex matches a
vendor-issued format with a recognizable prefix or wrapper. Arbitrary
high-entropy strings are intentionally out of scope to avoid noisy
false positives on fixtures, hashes, and encoded blobs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class CredentialFinding(object):
    """One match of a named credential or phishing pattern."""

    pattern_name: str
    redacted_preview: str


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ('aws_access_key_id', re.compile(r'\bAKIA[0-9A-Z]{16}\b')),
    ('github_pat_classic', re.compile(r'\bghp_[A-Za-z0-9]{36}\b')),
    ('github_pat_fine_grained', re.compile(r'\bgithub_pat_[A-Za-z0-9_]{82}\b')),
    ('github_oauth_token', re.compile(r'\bgh[osur]_[A-Za-z0-9]{36}\b')),
    ('openai_api_key_project', re.compile(r'\bsk-proj-[A-Za-z0-9_-]{20,}\b')),
    ('anthropic_api_key', re.compile(r'\bsk-ant-[A-Za-z0-9_-]{50,}\b')),
    ('google_api_key', re.compile(r'\bAIza[A-Za-z0-9_-]{35}\b')),
    ('slack_token', re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{10,}\b')),
    ('stripe_live_secret_key', re.compile(r'\bsk_live_[A-Za-z0-9]{24,}\b')),
    ('stripe_live_publishable_key', re.compile(r'\bpk_live_[A-Za-z0-9]{24,}\b')),
    ('pem_private_key_block', re.compile(r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----')),
    ('openssh_private_key_body', re.compile(r'\bOPENSSH PRIVATE KEY\b')),
)


def _redact(match_text: str) -> str:
    prefix_len = min(8, len(match_text))
    prefix = match_text[:prefix_len]
    return f'{prefix}…[REDACTED, total length={len(match_text)}]'


def find_credential_patterns(text: str) -> list[CredentialFinding]:
    """Return every named credential pattern matched in ``text``.

    The full matched value is never returned; callers get only the
    pattern name and a redacted preview that is safe to log.
    """
    if not text or not isinstance(text, str):
        return []
    findings: list[CredentialFinding] = []
    for pattern_name, regex in _PATTERNS:
        for match in regex.finditer(text):
            findings.append(
                CredentialFinding(
                    pattern_name=pattern_name,
                    redacted_preview=_redact(match.group(0)),
                )
            )
    return findings


def summarize_findings(findings: Iterable[CredentialFinding]) -> str:
    """Return an operator-facing summary without raw matched values."""
    by_name: dict[str, list[CredentialFinding]] = {}
    for finding in findings:
        by_name.setdefault(finding.pattern_name, []).append(finding)
    if not by_name:
        return 'no credential patterns detected'
    parts: list[str] = []
    for pattern_name, group in by_name.items():
        first = group[0].redacted_preview
        if len(group) == 1:
            parts.append(f'{pattern_name}={first}')
        else:
            parts.append(f'{pattern_name}={first} (+{len(group) - 1} more)')
    return '; '.join(parts)


PATTERN_NAMES: frozenset[str] = frozenset(name for name, _ in _PATTERNS)


_PHISHING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        'pipe_to_shell',
        re.compile(
            r'\b(?:curl|wget)\b[^\n|]{1,200}\|\s*(?:sudo\s+)?(?:bash|sh|zsh)\b'
        ),
    ),
    (
        'eval_remote_fetch',
        re.compile(
            r'(?:eval|bash\s+-c|sh\s+-c)\s*["\'`]?\$\([^)]*\b(?:curl|wget)\b'
        ),
    ),
    (
        'sudo_command',
        re.compile(r'(?:^|[`\n>;])\s*sudo\s+\S+', re.MULTILINE),
    ),
)


PHISHING_PATTERN_NAMES: frozenset[str] = frozenset(
    name for name, _ in _PHISHING_PATTERNS
)


def find_phishing_patterns(text: str) -> list[CredentialFinding]:
    """Return every named operator-phishing pattern matched in ``text``.

    Uses the same return shape as ``find_credential_patterns`` so
    output-side audit code can treat both detector families uniformly.
    """
    if not text or not isinstance(text, str):
        return []
    findings: list[CredentialFinding] = []
    for pattern_name, regex in _PHISHING_PATTERNS:
        for match in regex.finditer(text):
            findings.append(
                CredentialFinding(
                    pattern_name=pattern_name,
                    redacted_preview=_redact(match.group(0)),
                )
            )
    return findings
