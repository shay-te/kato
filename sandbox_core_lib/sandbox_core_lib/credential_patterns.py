"""High-confidence credential pattern detector.

Closes residual #18 (credential exfiltration via the legitimate
Anthropic egress channel) on two surfaces:

* **Pre-spawn workspace content scan** — extends
  ``scan_workspace_for_secrets`` so that an `.env`-shaped *name* is
  not the only signal; a high-confidence credential pattern in any
  file's content also blocks the spawn (subject to the existing
  ``KATO_SANDBOX_ALLOW_WORKSPACE_SECRETS=true`` operator override).
  Closes the case where a secret is committed to a file with an
  innocuous name (`config.yaml`, `README.md`, a migration file).

* **Post-spawn output scan** — the agent's final result text and the
  streaming session's terminal event are scanned. A match raises a
  WARNING-level log + an audit-log line (named pattern + redacted
  preview, never the raw value). Cannot undo the leak to Anthropic
  (the data has already crossed by the time we see the response),
  but produces an auditable record so the operator can rotate.

Patterns are kept narrow: each one matches a vendor-issued
credential format with a recognizable prefix or wrapper. We
deliberately do not try to detect arbitrary "looks like a token"
high-entropy strings — false positives on randomised test fixtures
and base64 blobs would make the operator ignore the warnings.

Adding a pattern is cheap; widening an existing pattern is not.
Each pattern's regex carries a name used in audit logs and warnings;
the same name appears in tests so a future loosening is caught.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class CredentialFinding(object):
    """One match of a named credential pattern.

    ``redacted_preview`` is safe to log and ship into audit lines —
    it shows enough to make the match recognisable to the operator
    without re-emitting the full credential value. The full match is
    never returned by the public API for the same reason.
    """

    pattern_name: str
    redacted_preview: str


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # AWS access key id — 20 chars, fixed prefix.
    ('aws_access_key_id', re.compile(r'\bAKIA[0-9A-Z]{16}\b')),
    # GitHub Personal Access Token (Classic).
    ('github_pat_classic', re.compile(r'\bghp_[A-Za-z0-9]{36}\b')),
    # GitHub Fine-grained PAT.
    ('github_pat_fine_grained', re.compile(r'\bgithub_pat_[A-Za-z0-9_]{82}\b')),
    # GitHub OAuth tokens (server-to-server, user-to-server, refresh).
    ('github_oauth_token', re.compile(r'\bgh[osur]_[A-Za-z0-9]{36}\b')),
    # OpenAI API key (project-scoped form).
    ('openai_api_key_project', re.compile(r'\bsk-proj-[A-Za-z0-9_-]{20,}\b')),
    # Anthropic API key.
    ('anthropic_api_key', re.compile(r'\bsk-ant-[A-Za-z0-9_-]{50,}\b')),
    # Google API key — fixed prefix + 35 url-safe chars.
    ('google_api_key', re.compile(r'\bAIza[A-Za-z0-9_-]{35}\b')),
    # Slack tokens — bot, user, app, refresh.
    ('slack_token', re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{10,}\b')),
    # Stripe live secret + publishable keys (test keys deliberately not flagged).
    ('stripe_live_secret_key', re.compile(r'\bsk_live_[A-Za-z0-9]{24,}\b')),
    ('stripe_live_publishable_key', re.compile(r'\bpk_live_[A-Za-z0-9]{24,}\b')),
    # PEM private key block — covers RSA, EC, DSA, OPENSSH, generic PRIVATE KEY.
    ('pem_private_key_block', re.compile(r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----')),
    # SSH private key body marker (defense-in-depth for the OPENSSH case
    # in case the BEGIN block was line-wrapped or the value was extracted
    # without the wrapper).
    ('openssh_private_key_body', re.compile(r'\bOPENSSH PRIVATE KEY\b')),
)


# Largest pattern length we ever match — used to size redaction previews
# and to avoid scanning chunks smaller than the longest possible match.
_MAX_PATTERN_LENGTH = 120


def _redact(match_text: str) -> str:
    """Return a preview safe to log: prefix + length + REDACTED tag.

    Showing the prefix lets the operator distinguish "AWS key starting
    with AKIAEXAMPLE…" from "AWS key starting with AKIAOTHER…" without
    re-emitting the secret. Length helps cross-reference with other
    audit sources (e.g. password manager logs).
    """
    prefix_len = min(8, len(match_text))
    prefix = match_text[:prefix_len]
    return f'{prefix}…[REDACTED, total length={len(match_text)}]'


def find_credential_patterns(text: str) -> list[CredentialFinding]:
    """Return every named credential pattern matched in ``text``.

    Each finding carries the pattern name and a redacted preview only;
    the full matched value is never included in the return value, so
    a caller that logs findings cannot accidentally re-emit a
    credential. Multiple matches of the same pattern produce multiple
    findings.

    Empty / non-string inputs return an empty list silently.
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
    """Operator-facing one-line summary of detector findings.

    Used in log messages, audit entries, and result-payload warnings.
    Never includes raw credential values — only counts per pattern
    and a short preview of the first match per pattern.
    """
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


# Names exported for tests so a future rename of a pattern is caught
# at import time, not by string-matching a log message.
PATTERN_NAMES: frozenset[str] = frozenset(name for name, _ in _PATTERNS)


# ----- Operator-phishing patterns (residual #16, defense-in-depth) -----
#
# Closes a slice of residual #16: the agent generates plausible-looking
# instructions that trick the operator into running something on their
# host. Kato handles infrastructure (git, build, push); the agent has
# no legitimate reason to ask the operator to run shell commands. So
# these patterns flag the most-suspicious phishing shapes and produce
# the same audit-trail signal as the credential filter.
#
# Bias toward HIGH-CONFIDENCE patterns: the agent might legitimately
# discuss installation in a code comment or a doc edit. We only flag
# patterns that have no defensible non-phishing use:
#
#   * ``curl ... | sh`` / ``curl ... | bash`` — the canonical
#     install-by-pipe trick. Zero legitimate use in agent output.
#   * ``wget ... | sh`` / ``wget ... | bash`` — same shape.
#   * ``eval "$(curl ...)"`` / ``bash -c "$(curl ...)"`` — same intent
#     wearing a different hat.
#   * ``sudo ...`` in shell-fenced text — sudo on the host is exactly
#     what kato exists to prevent. Any agent-produced sudo command
#     directed at the operator is suspicious by definition.
#
# Specifically NOT flagged (too noisy):
#   * Bare keywords like "install" or "run" — too many false positives
#     in legitimate prose.
#   * Code blocks that don't contain an execution-pipe pattern.
_PHISHING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Pipe-to-shell from a fetched URL. Both curl and wget shapes.
    (
        'pipe_to_shell',
        re.compile(
            r'\b(?:curl|wget)\b[^\n|]{1,200}\|\s*(?:sudo\s+)?(?:bash|sh|zsh)\b'
        ),
    ),
    # Shell substitution: eval "$(curl ...)" / bash -c "$(curl ...)".
    (
        'eval_remote_fetch',
        re.compile(
            r'(?:eval|bash\s+-c|sh\s+-c)\s*["\'`]?\$\([^)]*\b(?:curl|wget)\b'
        ),
    ),
    # Bare sudo command. The agent should never instruct the operator
    # to sudo on the host — kato handles privileged ops, agents don't.
    (
        'sudo_command',
        re.compile(r'(?:^|[`\n>;])\s*sudo\s+\S+', re.MULTILINE),
    ),
)


PHISHING_PATTERN_NAMES: frozenset[str] = frozenset(
    name for name, _ in _PHISHING_PATTERNS
)


def find_phishing_patterns(text: str) -> list[CredentialFinding]:
    """Return every named phishing pattern matched in ``text``.

    Same return shape as ``find_credential_patterns`` — operator
    handling code can treat both detector outputs uniformly. The
    pattern_name disambiguates (``credential_*`` vs ``pipe_to_shell``
    / ``sudo_command`` / ``eval_remote_fetch``).

    Like the credential detector, only the matched span's redacted
    preview is returned; the full match is never in the output, so
    log lines built from the result cannot accidentally re-emit the
    suspicious snippet at full fidelity.
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
