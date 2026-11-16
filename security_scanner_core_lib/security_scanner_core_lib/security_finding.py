"""Data types for the security-scanner pipeline.

A ``SecurityFinding`` is the atomic unit every runner produces. The
orchestrator collects them, dedupes by ``(tool, rule_id, path,
line)``, classifies severity against the operator's
``block_on_severity`` config, and decides whether to block the task.

Keep this file dependency-free — runners and consumers both import
from it, and pulling host services in here would invert the
dependency graph.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
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

    @classmethod
    def combined(cls, reports: Mapping[str, 'ScanReport']) -> 'ScanReport':
        """One report for several workspaces scanned apart, keyed by a label
        each (a repository's name). ``reports`` must not be empty.

        * Every finding's path starts with its label, with ``/`` between
          parts: a bare ``tests/data/.env`` did not say which of a task's
          seven repositories it was in, and a Windows ``tests\\data\\.env``
          lost a backslash in a markdown ticket comment.
        * A runner's warning is said once: a scanner that is not installed is
          not installed for every workspace, and the comment listed it once
          per repository.
        * Blocking is decided once, against the threshold the scans used.
        """
        findings = tuple(
            replace(finding, path=_labelled_path(label, finding.path))
            for label, report in reports.items()
            for finding in report.findings
        )
        threshold = next(iter(reports.values())).block_threshold
        return cls(
            findings=findings,
            blocking=any(f.severity.is_at_least(threshold) for f in findings),
            block_threshold=threshold,
            runner_errors=tuple(dict.fromkeys(
                error for report in reports.values() for error in report.runner_errors
            )),
        )

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


def _labelled_path(label: str, path: str) -> str:
    """``label/path`` with ``/`` between parts; just ``label`` for a finding
    about the whole workspace (``path`` empty, e.g. a dependency)."""
    parts = [part for part in (str(label or '').strip(), str(path or '').replace('\\', '/')) if part]
    return '/'.join(parts)
