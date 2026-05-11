"""Data types for the security-scanner pipeline.

A ``SecurityFinding`` is the atomic unit every runner produces. The
orchestrator collects them, dedupes by ``(tool, rule_id, path,
line)``, classifies severity against the operator's
``block_on_severity`` config, and decides whether to block the task.

Keep this file dependency-free — runners and consumers both import
from it, and pulling kato services in here would invert the
dependency graph.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class Severity(str, Enum):
    """Severity ladder. Inherits from ``str`` so JSON-serialisation
    preserves the human-readable name, not the enum repr.
    """
    CRITICAL = 'critical'
    HIGH = 'high'
    MEDIUM = 'medium'
    LOW = 'low'

    @classmethod
    def from_string(cls, value: str) -> 'Severity':
        """Parse case-insensitively. Unknown values fall back to LOW
        — the safe default that surfaces the finding without blocking.
        """
        if not value:
            return cls.LOW
        normalized = str(value).strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        return cls.LOW

    def __str__(self) -> str:
        return self.value

    def is_at_least(self, other: 'Severity') -> bool:
        """True when ``self`` >= ``other`` on the severity ladder.

        ``CRITICAL > HIGH > MEDIUM > LOW``.
        """
        order = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
        return order.index(self) >= order.index(other)


@dataclass(frozen=True)
class SecurityFinding:
    """One security issue surfaced by one runner.

    Frozen so we can put findings in a set for dedup. Equality is
    structural across all fields including ``metadata`` — two
    findings with different ``metadata`` count as distinct.
    """

    tool: str                    # 'detect-secrets', 'bandit', 'safety', etc.
    severity: Severity
    rule_id: str                 # plugin name / CVE id / bandit B-code
    message: str                 # human-readable, one line
    path: str = ''               # workspace-relative path; '' for repo-wide
    line: int = 0                # 0 when not applicable (dependency CVEs)
    metadata: tuple = field(default_factory=tuple)
    # ``metadata`` is a tuple of (key, value) pairs so the dataclass
    # stays hashable. Use ``dict(finding.metadata)`` to read it back.

    def dedup_key(self) -> tuple[str, str, str, int]:
        """The 4-tuple the orchestrator uses to drop duplicates."""
        return (self.tool, self.rule_id, self.path, self.line)

    def to_dict(self) -> dict:
        """JSON-friendly view; severity becomes its string value."""
        out = asdict(self)
        out['severity'] = self.severity.value
        out['metadata'] = dict(self.metadata)
        return out


@dataclass(frozen=True)
class ScanReport:
    """Aggregate result of one workspace scan.

    The orchestrator returns this; callers (preflight, notification,
    UI) read its top-level booleans rather than re-implementing the
    severity-threshold logic each time.
    """

    findings: tuple[SecurityFinding, ...]
    blocking: bool                # True when at least one finding is at-or-above the block threshold
    block_threshold: Severity     # the configured threshold this scan was evaluated against
    runner_errors: tuple[tuple[str, str], ...] = ()
    # ``runner_errors`` lists ``(runner_name, error_message)`` for
    # runners that crashed or timed out. They DON'T block the task —
    # an infrastructure flake (network, missing tool) shouldn't
    # silently halt the operator. They surface in the report as
    # warnings so the operator notices.

    def by_severity(self, severity: Severity) -> tuple[SecurityFinding, ...]:
        return tuple(f for f in self.findings if f.severity == severity)

    def has_findings_at_least(self, severity: Severity) -> bool:
        return any(f.severity.is_at_least(severity) for f in self.findings)

    def to_dict(self) -> dict:
        return {
            'blocking': self.blocking,
            'block_threshold': self.block_threshold.value,
            'findings': [f.to_dict() for f in self.findings],
            'runner_errors': [
                {'runner': name, 'error': err}
                for name, err in self.runner_errors
            ],
        }
