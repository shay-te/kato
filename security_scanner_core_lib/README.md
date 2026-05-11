# security-scanner-core-lib

Pre-execution workspace security scanner. Walks a per-task
workspace before an agent spawns and surfaces credential leaks,
dangerous patterns, vulnerable dependencies, and `.env`-files-on-disk.

## What lives here

```
security_scanner_core_lib/security_scanner_core_lib/
├── scanner_provider.py             ← ScannerProvider Protocol
├── security_finding.py             ← SecurityFinding + ScanReport + Severity DTOs
├── security_scanner_service.py     ← orchestrator: runs runners + dedupes + decides block/warn
└── runners/
    ├── _helpers.py                  ← RunnerUnavailableError + workspace-relative path utils
    ├── bandit_runner.py             ← Python static analysis
    ├── detect_secrets_runner.py     ← credential scanner
    ├── env_file_runner.py           ← `.env` files on disk
    ├── npm_audit_runner.py          ← npm dependency CVE check
    └── safety_runner.py             ← Python dependency CVE check
```

## The contract

Every runner is a module exposing one function:

```python
from security_scanner_core_lib.security_scanner_core_lib.scanner_provider import (
    ScannerProvider,  # Protocol
)
from security_scanner_core_lib.security_scanner_core_lib.security_finding import (
    SecurityFinding, Severity,
)


class _MyScanner:
    def run(self, workspace_path, logger=None, timeout_seconds=None) -> list[SecurityFinding]:
        ...
```

Five runners ship today; new ones drop in by matching the Protocol —
no inheritance, no class wrapper. Same on-ramp pattern as
`vcs_provider_contracts.PullRequestProvider` and
`agent_provider_contracts.AgentProvider`.

## Public surface

```python
from security_scanner_core_lib.security_scanner_core_lib import (
    SecurityScannerService,  # orchestrator
    ScannerProvider,         # Protocol every runner satisfies
    SecurityFinding,         # DTO for one finding
    ScanReport,              # DTO for the aggregated result
    Severity,                # CRITICAL / HIGH / MEDIUM / LOW / INFO
    SecurityScanBlocked,     # raised when the scan blocks task exec
    RunnerUnavailableError,  # raised when a tool isn't installed
)
```

## Where this fits in kato

`TaskPreflightService` wires the orchestrator after workspace
clones land but before the agent spawns. `CRITICAL`/`HIGH`
findings raise `SecurityScanBlocked`; the existing failure-handler
chain catches it. `MEDIUM`/`LOW` findings are logged and the
task proceeds.

## Why a separate package

Five real runner implementations behind one contract — the
strongest provider-shape signal of any candidate that came up.
Plausible second consumer: any agent-orchestration tool wanting
"scan a workspace before running an agent" gets the entire
runner pack + the ScannerProvider contract for free. New
scanners (semgrep, trufflehog, custom rule packs) drop in
without touching kato.

## Tests

```
security_scanner_core_lib/security_scanner_core_lib/tests/
```

Covers the orchestrator (runner timeouts, dedupe, severity
thresholds, block decision), each runner's parser + severity
mapping, and the ScannerProvider Protocol drift guard.
