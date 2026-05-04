"""Run ``detect-secrets`` against the workspace.

Wraps Yelp's ``detect-secrets`` plugin scanner. The library does the
hard work — entropy analysis, regex matching, base64 detection — and
exposes a stable Python API. We turn its ``PotentialSecret`` records
into kato ``SecurityFinding``s and map plugin names to severities.

Severity mapping rationale:
- Live-cloud / private-key / well-known-token plugins → ``CRITICAL``.
  These have very low false-positive rates; a hit is almost
  certainly a real leaked credential.
- Generic high-entropy / base64 plugins → ``HIGH``. Higher false-
  positive rate (test fixtures, hashes, git SHAs), but still worth
  surfacing — a leaked random-looking token costs the same as a
  pattern-matched one.

When ``detect-secrets`` isn't installed, raises
``RunnerUnavailableError`` so the orchestrator surfaces it as a
warning rather than a security finding. Operators who don't want
this runner can ``pip uninstall detect-secrets``.
"""

from __future__ import annotations

import logging
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


# ``detect-secrets`` plugin types → severity. Plugin names come from
# the ``secret_type`` field on each ``PotentialSecret``.
_PLUGIN_SEVERITY: dict[str, Severity] = {
    'AWS Access Key':                    Severity.CRITICAL,
    'AWS Sensitive Information':         Severity.CRITICAL,
    'Azure Storage Account access key':  Severity.CRITICAL,
    'GCP API Key':                       Severity.CRITICAL,
    'Private Key':                       Severity.CRITICAL,
    'Stripe Access Key':                 Severity.CRITICAL,
    'GitHub Token':                      Severity.CRITICAL,
    'GitLab Token':                      Severity.CRITICAL,
    'Slack Token':                       Severity.CRITICAL,
    'JSON Web Token':                    Severity.HIGH,
    'Mailchimp Access Key':              Severity.CRITICAL,
    'Twilio API Key':                    Severity.CRITICAL,
    'Square OAuth Secret':               Severity.CRITICAL,
    'SendGrid API Key':                  Severity.CRITICAL,
    'NPM tokens':                        Severity.CRITICAL,
    'IBM Cloud IAM Key':                 Severity.CRITICAL,
    'IBM COS HMAC Credentials':          Severity.CRITICAL,
    'Discord Bot Token':                 Severity.CRITICAL,
    'Cloudant Credentials':              Severity.CRITICAL,
    'Artifactory Credentials':           Severity.CRITICAL,
    'Basic Auth Credentials':            Severity.HIGH,
    'OpenAI Token':                      Severity.CRITICAL,
    'Telegram Bot Token':                Severity.CRITICAL,
    # Generic / heuristic plugins — higher FP rate, lower severity.
    'Secret Keyword':                    Severity.MEDIUM,
    'Hex High Entropy String':           Severity.HIGH,
    'Base64 High Entropy String':        Severity.HIGH,
}


def _severity_for(secret_type: str) -> Severity:
    """Look up severity, defaulting to ``HIGH`` for unknown plugin types.

    New plugins added to detect-secrets between releases get the
    ``HIGH`` floor — better to warn loudly on something we don't
    recognise than silently downgrade to LOW.
    """
    return _PLUGIN_SEVERITY.get(secret_type, Severity.HIGH)


def run(
    workspace_path: str,
    logger: logging.Logger | None = None,
) -> list[SecurityFinding]:
    try:
        from detect_secrets import SecretsCollection
        from detect_secrets.settings import default_settings
    except ImportError as exc:
        raise RunnerUnavailableError(
            'detect-secrets is not installed. Run '
            '`pip install detect-secrets` to enable this scanner, '
            'or remove it from the runner config to silence this warning.'
        ) from exc
    workspace = Path(workspace_path)
    if not workspace.is_dir():
        return []
    findings: list[SecurityFinding] = []
    secrets = SecretsCollection()
    with default_settings():
        # ``scan_files`` would let us pre-filter, but ``scan_diff`` /
        # ``scan_file`` per-file calls give us tighter control over
        # the EXCLUDE_DIRS skip than the library's built-in walk.
        for path in _files_to_scan(workspace):
            try:
                secrets.scan_file(str(path))
            except Exception as exc:  # detect-secrets occasionally chokes on binary files
                if logger is not None:
                    logger.debug(
                        'detect-secrets: skipping %s due to scan error: %s',
                        path, exc,
                    )
                continue
    for filename, secret in secrets:
        secret_type = getattr(secret, 'type', '') or 'unknown'
        line_no = getattr(secret, 'line_number', 0) or 0
        path_str = workspace_relative(workspace, Path(filename))
        findings.append(SecurityFinding(
            tool='detect-secrets',
            severity=_severity_for(secret_type),
            rule_id=secret_type,
            message=(
                f'{secret_type} detected at line {line_no}. If this is a '
                f'test fixture, consider using ``# pragma: allowlist secret`` '
                f'inline. Otherwise rotate the credential and remove it '
                f'from history.'
            ),
            path=path_str,
            line=line_no,
            metadata=(('secret_type', secret_type),),
        ))
    return findings


def _files_to_scan(workspace: Path):
    """Honour EXCLUDE_DIRS while letting detect-secrets see everything else.

    We don't pre-filter by extension — detect-secrets has its own
    binary-file detection and entropy plugins benefit from seeing
    config files, certificates, scripts, etc. all uniformly.
    """
    for child in workspace.iterdir():
        if child.is_dir():
            if child.name in EXCLUDE_DIRS:
                continue
            yield from _files_to_scan(child)
        elif child.is_file():
            yield child
