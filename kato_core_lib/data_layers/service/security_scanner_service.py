"""Pre-execution workspace security scanner.

Runs the configured set of runners against a per-task workspace,
collects ``SecurityFinding``s, dedupes them, and decides whether the
task should be blocked. Used by ``TaskPreflightService`` after the
workspace is cloned but before the agent is spawned.

Runners are pluggable via ``runners_config``; each gets its own
timeout (a stuck scanner can't halt the operator forever) and is
isolated in a thread so a hang in one doesn't cascade.

Output:
- ``ScanReport`` with all findings + a top-level ``blocking`` bool
- The orchestrator does NOT call notification / ticketing — it
  returns the report and lets the preflight caller decide what to
  do (block + notify, warn + comment, or silent log). Keeping this
  side-effect-free makes it trivial to test and to call from
  contexts that don't need the full notification flow.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Callable

from kato_core_lib.data_layers.data.security_finding import (
    ScanReport,
    SecurityFinding,
    Severity,
)
from kato_core_lib.data_layers.service.security_scanner_runners._helpers import (
    RunnerUnavailableError,
)


# Runner contract: ``(workspace_path, logger) -> list[SecurityFinding]``.
# Runners may also accept ``timeout_seconds`` as a keyword arg; the
# orchestrator passes it when present.
RunnerFn = Callable[..., list[SecurityFinding]]


class SecurityScanBlocked(RuntimeError):
    """Raised by the preflight when a scan blocks task execution.

    Carries the ``ScanReport`` so the failure handler can format a
    detailed ticket comment + email body. Inherits ``RuntimeError``
    so unaware ``except Exception`` blocks still catch it.
    """

    def __init__(self, report: ScanReport) -> None:
        self.report = report
        # Short message for the default ``str(exc)`` rendering (used
        # when the failure handler isn't security-aware). The
        # detailed markdown lives in ``summarize_for_ticket``.
        critical_count = len(report.by_severity(Severity.CRITICAL))
        high_count = len(report.by_severity(Severity.HIGH))
        super().__init__(
            f'security scan blocked task execution: '
            f'{critical_count} critical, {high_count} high finding(s). '
            f'See ticket comment for details.'
        )


@dataclass
class RunnerConfig:
    name: str
    fn: RunnerFn
    timeout_seconds: int = 120
    enabled: bool = True


@dataclass
class SecurityScannerConfig:
    enabled: bool = True
    block_on_severity: tuple[Severity, ...] = (Severity.CRITICAL, Severity.HIGH)
    runners: list[RunnerConfig] = field(default_factory=list)

    def block_threshold(self) -> Severity:
        """Lowest severity in ``block_on_severity`` (i.e. the gate)."""
        if not self.block_on_severity:
            return Severity.CRITICAL
        order = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
        present = [s for s in order if s in self.block_on_severity]
        return present[0] if present else Severity.CRITICAL


class SecurityScannerService:
    def __init__(
        self,
        config: SecurityScannerConfig | None = None,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config or default_config()
        self.logger = logger or logging.getLogger(self.__class__.__name__)

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def scan_workspace(self, workspace_path: str) -> ScanReport:
        """Run every enabled runner and return the aggregate report.

        Disabled scanner → empty, non-blocking report (the operator
        explicitly turned it off; respect that without log noise).
        """
        if not self._config.enabled:
            return ScanReport(
                findings=(),
                blocking=False,
                block_threshold=self._config.block_threshold(),
            )
        all_findings: list[SecurityFinding] = []
        runner_errors: list[tuple[str, str]] = []
        # One thread per runner — most runners are I/O-bound (subprocess
        # calls, network for safety/npm), so threads are appropriate.
        # Keep the pool small to bound resource use.
        active = [r for r in self._config.runners if r.enabled]
        if not active:
            return ScanReport(
                findings=(),
                blocking=False,
                block_threshold=self._config.block_threshold(),
            )
        with ThreadPoolExecutor(max_workers=min(4, len(active))) as pool:
            futures = {
                pool.submit(self._invoke_runner, runner, workspace_path): runner
                for runner in active
            }
            for future, runner in futures.items():
                try:
                    # Future timeout = runner's own budget. Subprocess
                    # runners honour the budget internally and return
                    # early on timeout; pure-Python runners that ignore
                    # the kwarg get cut off here. Either way, the budget
                    # is the contract — no hidden +N seconds buffer.
                    findings = future.result(timeout=runner.timeout_seconds)
                except FutureTimeout:
                    runner_errors.append((
                        runner.name,
                        f'runner exceeded timeout of {runner.timeout_seconds}s',
                    ))
                    self.logger.warning(
                        'security scanner: runner %s timed out after %ss',
                        runner.name, runner.timeout_seconds,
                    )
                    continue
                except RunnerUnavailableError as exc:
                    runner_errors.append((runner.name, str(exc)))
                    self.logger.info(
                        'security scanner: %s unavailable, skipping (%s)',
                        runner.name, exc,
                    )
                    continue
                except Exception as exc:
                    runner_errors.append((runner.name, str(exc)))
                    self.logger.exception(
                        'security scanner: runner %s crashed', runner.name,
                    )
                    continue
                all_findings.extend(findings)
        deduped = _dedupe(all_findings)
        threshold = self._config.block_threshold()
        blocking = any(f.severity.is_at_least(threshold) for f in deduped)
        return ScanReport(
            findings=tuple(deduped),
            blocking=blocking,
            block_threshold=threshold,
            runner_errors=tuple(runner_errors),
        )

    def _invoke_runner(
        self, runner: RunnerConfig, workspace_path: str,
    ) -> list[SecurityFinding]:
        """Call the runner with timeout-aware kwargs when supported.

        Some runners (subprocess-based) accept their own
        ``timeout_seconds``; pass it through so the runner can also
        bound its child process. Pure-Python runners that don't
        accept the kwarg are called without it.
        """
        try:
            return runner.fn(
                workspace_path,
                logger=self.logger,
                timeout_seconds=runner.timeout_seconds,
            )
        except TypeError:
            return runner.fn(workspace_path, logger=self.logger)

    # ----- summarisers (used by callers to build comments / emails) -----

    @staticmethod
    def summarize_for_ticket(report: ScanReport) -> str:
        """Markdown body suitable for a YouTrack / Jira ticket comment."""
        lines: list[str] = []
        if report.blocking:
            lines.append('🛑 **kato refused this task: security scan found '
                         'CRITICAL/HIGH issues.**')
        elif report.findings:
            lines.append('⚠️ **kato security scan found MEDIUM/LOW findings.** '
                         'The task will proceed; please review.')
        else:
            lines.append('✅ kato security scan: no findings.')
        if report.findings:
            lines.append('')
            lines.append('| severity | tool | path | rule | message |')
            lines.append('|---|---|---|---|---|')
            for f in sorted(
                report.findings,
                key=lambda x: (-_severity_weight(x.severity), x.tool, x.path),
            ):
                path_cell = f'{f.path}:{f.line}' if f.line else f.path or '—'
                msg = f.message.replace('|', '\\|').replace('\n', ' ')
                lines.append(
                    f'| {f.severity.value} | {f.tool} | {path_cell} | '
                    f'{f.rule_id} | {msg} |'
                )
        if report.runner_errors:
            lines.append('')
            lines.append('Scanner warnings (non-blocking):')
            for name, err in report.runner_errors:
                lines.append(f'- {name}: {err}')
        return '\n'.join(lines)

    @staticmethod
    def summarize_for_email(report: ScanReport) -> tuple[str, str]:
        """``(subject, body)`` pair for the security distribution list.

        Subject is short + scannable; body is the same markdown
        used in the ticket comment, with a one-line preamble for
        operators reading it in their inbox.
        """
        if report.blocking:
            subject = (
                f'[kato] BLOCKED: {len(report.findings)} security finding(s) '
                f'(threshold: {report.block_threshold.value})'
            )
        elif report.findings:
            subject = (
                f'[kato] WARN: {len(report.findings)} security finding(s)'
            )
        else:
            subject = '[kato] security scan clean'
        body = SecurityScannerService.summarize_for_ticket(report)
        return subject, body


def _severity_weight(severity: Severity) -> int:
    return {
        Severity.CRITICAL: 4,
        Severity.HIGH: 3,
        Severity.MEDIUM: 2,
        Severity.LOW: 1,
    }[severity]


def _dedupe(findings: list[SecurityFinding]) -> list[SecurityFinding]:
    """Drop duplicates by ``(tool, rule_id, path, line)`` — same
    finding from two runs of the same runner shouldn't double-bill.
    Preserves first-seen order so the report stays stable.
    """
    seen: set[tuple] = set()
    out: list[SecurityFinding] = []
    for f in findings:
        key = f.dedup_key()
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def default_config() -> SecurityScannerConfig:
    """Default runner set — every runner enabled with sane timeouts.

    Operators override per-runner enable / timeout via
    ``kato.security_scanner.runners`` in the config file or by
    passing a custom ``SecurityScannerConfig`` to the constructor.
    """
    from kato_core_lib.data_layers.service.security_scanner_runners import (
        bandit_runner,
        detect_secrets_runner,
        env_file_runner,
        npm_audit_runner,
        safety_runner,
    )
    return SecurityScannerConfig(
        enabled=True,
        block_on_severity=(Severity.CRITICAL, Severity.HIGH),
        runners=[
            RunnerConfig('env-file', env_file_runner.run, timeout_seconds=30),
            RunnerConfig('detect-secrets', detect_secrets_runner.run, timeout_seconds=60),
            RunnerConfig('bandit', bandit_runner.run, timeout_seconds=120),
            RunnerConfig('safety', safety_runner.run, timeout_seconds=120),
            RunnerConfig('npm-audit', npm_audit_runner.run, timeout_seconds=120),
        ],
    )
