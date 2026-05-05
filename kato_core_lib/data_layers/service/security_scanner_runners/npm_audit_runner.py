"""Run ``npm audit`` against Node.js dependency manifests.

Walks the workspace for directories containing both
``package.json`` and one of ``package-lock.json`` / ``yarn.lock``,
then runs ``npm audit --json`` in each and parses the report.

We use ``npm audit`` (not ``yarn audit``) regardless of which
lockfile the project uses — npm reads yarn.lock fine since v7, and
running one tool keeps the runner simple.

Severity comes straight from npm's advisory database (npm uses the
same severity ladder as kato), with one tweak: npm emits ``info``
which we map to ``LOW``.

When ``npm`` isn't on PATH, raises ``RunnerUnavailableError`` —
projects without Node.js dependencies will never have ``npm`` and
shouldn't see warnings. Operators with Node projects who don't want
this runner can ``npm uninstall -g`` or remove it from runner
config.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from kato_core_lib.data_layers.data.security_finding import (
    SecurityFinding,
    Severity,
)
from kato_core_lib.data_layers.service.security_scanner_runners._helpers import (
    EXCLUDE_DIRS,
    RunnerUnavailableError,
    workspace_relative,
)


_NPM_TO_KATO_SEVERITY = {
    'critical': Severity.CRITICAL,
    'high':     Severity.HIGH,
    'moderate': Severity.MEDIUM,
    'low':      Severity.LOW,
    'info':     Severity.LOW,
}


def _find_npm_projects(workspace: Path):
    """Yield every directory under ``workspace`` that has a
    ``package.json`` plus a lockfile (``package-lock.json`` or
    ``yarn.lock``). Monorepos with multiple ``package.json`` files
    are scanned independently per project.
    """
    for child in workspace.iterdir():
        if child.is_dir():
            if child.name in EXCLUDE_DIRS:
                continue
            yield from _find_npm_projects(child)
            continue
    if (workspace / 'package.json').is_file() and (
        (workspace / 'package-lock.json').is_file()
        or (workspace / 'yarn.lock').is_file()
    ):
        yield workspace


def run(
    workspace_path: str,
    logger: logging.Logger | None = None,
    *,
    timeout_seconds: int = 120,
) -> list[SecurityFinding]:
    if shutil.which('npm') is None:
        raise RunnerUnavailableError(
            'npm is not installed. Install Node.js (npm ships with '
            'it) to enable this scanner, or remove it from the '
            'runner config to silence this warning.'
        )
    workspace = Path(workspace_path)
    if not workspace.is_dir():
        return []
    findings: list[SecurityFinding] = []
    for project_dir in _find_npm_projects(workspace):
        cmd = ['npm', 'audit', '--json']
        try:
            result = subprocess.run(
                cmd, cwd=str(project_dir),
                capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=timeout_seconds, check=False,
            )
        except FileNotFoundError as exc:
            raise RunnerUnavailableError(
                f'npm binary disappeared between which() and run(): {exc}',
            ) from exc
        # npm audit returns 0 when clean, 1+ when vulns present —
        # JSON is always on stdout regardless.
        if not (result.stdout or '').strip():
            if logger is not None:
                logger.warning(
                    'npm audit produced no JSON in %s: %s',
                    project_dir,
                    (result.stderr or '').strip()[:500],
                )
            continue
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            if logger is not None:
                logger.warning(
                    'npm audit produced non-JSON in %s: %s',
                    project_dir, exc,
                )
            continue
        # npm v7+ shape: ``vulnerabilities`` is a dict keyed by
        # package name; each entry has ``severity`` + nested ``via``
        # advisories. Older shapes had a flat ``advisories`` dict —
        # handle both, prefer v7+.
        rel_path = workspace_relative(workspace, project_dir / 'package.json')
        vulns = payload.get('vulnerabilities')
        if isinstance(vulns, dict):
            findings.extend(_findings_from_v7(vulns, rel_path))
            continue
        advisories = payload.get('advisories')
        if isinstance(advisories, dict):
            findings.extend(_findings_from_legacy(advisories, rel_path))
    return findings


def _findings_from_v7(vulns: dict, manifest_path: str) -> list[SecurityFinding]:
    """Parse npm v7+ ``vulnerabilities`` into kato findings."""
    out: list[SecurityFinding] = []
    seen: set[tuple[str, str]] = set()
    for package_name, entry in vulns.items():
        severity_raw = str(entry.get('severity', 'low'))
        severity = _NPM_TO_KATO_SEVERITY.get(severity_raw, Severity.LOW)
        for via in entry.get('via', []):
            if not isinstance(via, dict):
                continue
            advisory_id = str(via.get('source') or via.get('url') or '')
            if not advisory_id:
                continue
            key = (package_name, advisory_id)
            if key in seen:
                continue
            seen.add(key)
            title = str(via.get('title') or via.get('name') or 'unknown advisory')
            range_str = str(via.get('range', '') or entry.get('range', ''))
            out.append(SecurityFinding(
                tool='npm-audit',
                severity=severity,
                rule_id=advisory_id,
                message=(
                    f'{package_name} ({range_str}): {title}'
                    if range_str else f'{package_name}: {title}'
                ),
                path=manifest_path,
                line=0,
                metadata=(
                    ('package', package_name),
                    ('npm_severity', severity_raw),
                    ('range', range_str),
                ),
            ))
    return out


def _findings_from_legacy(advisories: dict, manifest_path: str) -> list[SecurityFinding]:
    """Parse npm v6-style ``advisories`` into kato findings."""
    out: list[SecurityFinding] = []
    for advisory_id, entry in advisories.items():
        severity_raw = str(entry.get('severity', 'low'))
        severity = _NPM_TO_KATO_SEVERITY.get(severity_raw, Severity.LOW)
        package = str(entry.get('module_name', '?'))
        title = str(entry.get('title', 'unknown advisory'))
        out.append(SecurityFinding(
            tool='npm-audit',
            severity=severity,
            rule_id=str(advisory_id),
            message=f'{package}: {title}',
            path=manifest_path,
            line=0,
            metadata=(
                ('package', package),
                ('npm_severity', severity_raw),
            ),
        ))
    return out
