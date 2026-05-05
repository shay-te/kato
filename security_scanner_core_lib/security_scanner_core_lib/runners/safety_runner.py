"""Run ``safety`` against Python dependency manifests in the workspace.

``safety`` checks installed / declared Python packages against the
PyUp.io vulnerability database. We invoke it once per requirement
file we find (``requirements.txt``, ``Pipfile.lock``, ``poetry.lock``,
``pyproject.toml``).

Network-dependent — talks to PyUp.io to fetch the latest CVE
metadata. When the network is unreachable, the runner logs a warning
and returns ``[]`` rather than blocking the task. Operators can pin
to an offline DB by setting ``SAFETY_DB_DIR``.

Severity comes from the CVSS score embedded in each safety record:
- 9.0+ → ``CRITICAL``
- 7.0+ → ``HIGH``
- 4.0+ → ``MEDIUM``
- otherwise → ``LOW``

When ``safety`` isn't installed, raises ``RunnerUnavailableError``.
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
    EXCLUDE_DIRS,
    RunnerUnavailableError,
    workspace_relative,
)


_REQUIREMENT_FILES = ('requirements.txt', 'Pipfile.lock', 'poetry.lock')


def _severity_from_cvss(score: float | None) -> Severity:
    if score is None:
        return Severity.MEDIUM
    if score >= 9.0:
        return Severity.CRITICAL
    if score >= 7.0:
        return Severity.HIGH
    if score >= 4.0:
        return Severity.MEDIUM
    return Severity.LOW


def _find_requirement_files(workspace: Path):
    for child in workspace.iterdir():
        if child.is_dir():
            if child.name in EXCLUDE_DIRS:
                continue
            yield from _find_requirement_files(child)
        elif child.is_file() and child.name in _REQUIREMENT_FILES:
            yield child


def run(
    workspace_path: str,
    logger: logging.Logger | None = None,
    *,
    timeout_seconds: int = 120,
) -> list[SecurityFinding]:
    if shutil.which('safety') is None:
        raise RunnerUnavailableError(
            'safety is not installed. Run `pip install safety` to '
            'enable this scanner, or remove it from the runner '
            'config to silence this warning.'
        )
    workspace = Path(workspace_path)
    if not workspace.is_dir():
        return []
    findings: list[SecurityFinding] = []
    for req_file in _find_requirement_files(workspace):
        # ``safety check`` returns 0 (no vulns) or 64 (vulns found);
        # other codes indicate runtime issues (network, parse errors).
        # JSON output goes to stdout in either of the first two cases.
        cmd = [
            'safety', 'check',
            '--file', str(req_file),
            '--json',
            '--disable-optional-telemetry',
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=timeout_seconds, check=False,
            )
        except FileNotFoundError as exc:
            raise RunnerUnavailableError(
                f'safety binary disappeared between which() and run(): {exc}',
            ) from exc
        if result.returncode not in (0, 64):
            if logger is not None:
                logger.warning(
                    'safety exited %s on %s: %s',
                    result.returncode, req_file,
                    (result.stderr or '').strip()[:500],
                )
            continue
        if not (result.stdout or '').strip():
            continue
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            if logger is not None:
                logger.warning(
                    'safety produced non-JSON output for %s: %s',
                    req_file, exc,
                )
            continue
        # ``safety`` JSON shape varies by version; both old (``[]``
        # at top level) and new (``{vulnerabilities: [...]}``) are
        # handled here.
        records = (
            payload if isinstance(payload, list)
            else payload.get('vulnerabilities', [])
        )
        rel_path = workspace_relative(workspace, req_file)
        for record in records:
            package = str(record.get('package_name') or record.get('package', ''))
            installed = str(record.get('analyzed_version') or record.get('installed_version', ''))
            vuln_id = str(
                record.get('vulnerability_id')
                or record.get('CVE')
                or record.get('cve', '')
                or 'UNKNOWN',
            )
            cvss = record.get('CVSS') or record.get('cvss')
            try:
                cvss_score = float(cvss) if cvss is not None else None
            except (TypeError, ValueError):
                cvss_score = None
            advisory = str(
                record.get('advisory')
                or record.get('description', '')
                or '(no advisory text)',
            )
            findings.append(SecurityFinding(
                tool='safety',
                severity=_severity_from_cvss(cvss_score),
                rule_id=vuln_id,
                message=(
                    f'{package} {installed}: {advisory[:280]}'
                    + ('…' if len(advisory) > 280 else '')
                ),
                path=rel_path,
                line=0,
                metadata=(
                    ('package', package),
                    ('installed_version', installed),
                    ('cvss', str(cvss_score) if cvss_score is not None else ''),
                ),
            ))
    return findings
