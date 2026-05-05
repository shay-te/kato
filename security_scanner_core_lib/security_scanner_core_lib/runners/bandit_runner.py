"""Run ``bandit`` against Python code in the workspace.

Wraps the Python static-analysis tool. We run bandit as a
subprocess (rather than its Python API) because bandit's API is
stateful at the global level — running multiple bandit scans in the
same process can leak config — and the orchestrator already isolates
runners with timeouts at the subprocess boundary.

Severity mapping is ``bandit_severity → kato_severity``:
- ``HIGH`` → ``HIGH``        (e.g. hardcoded password match, use of ``pickle.loads`` on user input)
- ``MEDIUM`` → ``MEDIUM``    (e.g. ``eval``, weak hash, ``yaml.load`` without safe loader)
- ``LOW`` → ``LOW``          (informational; e.g. ``assert`` in non-test code)

Confidence is folded in: bandit's ``LOW`` confidence on a
``HIGH``-severity finding gets demoted one notch to ``MEDIUM``,
trading false-positive volume for blocking signal.

When ``bandit`` isn't installed, raises ``RunnerUnavailableError``.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from security_scanner_core_lib.security_scanner_core_lib.security_finding import (
    SecurityFinding,
    Severity,
)
from security_scanner_core_lib.security_scanner_core_lib.runners._helpers import (
    RunnerUnavailableError,
    workspace_relative,
)


# Bandit's --exclude takes a comma-separated list of glob patterns.
# Skip the standard cache/build/test directories so we don't drown
# operators in test-fixture findings.
_BANDIT_EXCLUDE_DIRS = ','.join([
    '.git', '.hg', '.svn', '.venv', 'venv', '.tox', '.mypy_cache',
    '.pytest_cache', '.ruff_cache', '__pycache__', 'dist', 'build',
    'node_modules', 'tests', 'test',
])


_BANDIT_TO_KATO_SEVERITY = {
    'HIGH':   Severity.HIGH,
    'MEDIUM': Severity.MEDIUM,
    'LOW':    Severity.LOW,
}


def _kato_severity(bandit_severity: str, confidence: str) -> Severity:
    """Map bandit's (severity, confidence) → kato severity.

    Demote one notch when confidence is ``LOW`` so noisy heuristics
    don't block tasks. Confidence ``HIGH`` / ``MEDIUM`` keeps the
    raw severity.
    """
    base = _BANDIT_TO_KATO_SEVERITY.get(
        bandit_severity.upper(), Severity.LOW,
    )
    if confidence.upper() == 'LOW':
        if base == Severity.HIGH:
            return Severity.MEDIUM
        if base == Severity.MEDIUM:
            return Severity.LOW
    return base


def run(
    workspace_path: str,
    logger: logging.Logger | None = None,
    *,
    timeout_seconds: int = 120,
) -> list[SecurityFinding]:
    if shutil.which('bandit') is None:
        raise RunnerUnavailableError(
            'bandit is not installed. Run `pip install bandit` to '
            'enable this scanner, or remove it from the runner '
            'config to silence this warning.'
        )
    workspace = Path(workspace_path)
    if not workspace.is_dir():
        return []
    cmd = [
        'bandit',
        '-r', str(workspace),
        '-f', 'json',
        '--severity-level', 'low',
        '--confidence-level', 'low',
        '--exclude', _BANDIT_EXCLUDE_DIRS,
        '-q',
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=timeout_seconds, check=False,
        )
    except FileNotFoundError as exc:
        raise RunnerUnavailableError(
            f'bandit binary disappeared between which() and run(): {exc}'
        ) from exc
    # bandit returns:
    #   0  → no findings
    #   1  → findings present (NOT an error — JSON is on stdout)
    #   >1 → real error (config issue, etc.)
    # We accept 0/1, log >1 as a runner error via the orchestrator.
    if result.returncode not in (0, 1):
        if logger is not None:
            logger.warning(
                'bandit exited %s: %s',
                result.returncode,
                (result.stderr or '').strip()[:500],
            )
        return []
    if not (result.stdout or '').strip():
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        if logger is not None:
            logger.warning('bandit produced non-JSON output: %s', exc)
        return []
    findings: list[SecurityFinding] = []
    for raw in payload.get('results', []):
        bandit_severity = str(raw.get('issue_severity', '') or 'LOW')
        confidence = str(raw.get('issue_confidence', '') or 'LOW')
        rule_id = str(raw.get('test_id', '') or 'B000')
        rule_name = str(raw.get('test_name', '') or rule_id)
        message = str(raw.get('issue_text', '') or rule_name)
        filename = str(raw.get('filename', '') or '')
        line_no = int(raw.get('line_number', 0) or 0)
        findings.append(SecurityFinding(
            tool='bandit',
            severity=_kato_severity(bandit_severity, confidence),
            rule_id=rule_id,
            message=f'{rule_name}: {message}',
            path=workspace_relative(workspace, Path(filename)) if filename else '',
            line=line_no,
            metadata=(
                ('bandit_severity', bandit_severity),
                ('confidence', confidence),
            ),
        ))
    return findings
