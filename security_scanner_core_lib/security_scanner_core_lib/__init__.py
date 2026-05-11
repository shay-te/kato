"""Pre-execution workspace security scanner.

Multi-runner scanner that walks a per-task workspace before an
agent spawns and surfaces credential leaks, dangerous patterns,
vulnerable dependencies, and `.env`-files-on-disk. Every runner
is plug-pluggable behind one ``ScannerProvider`` contract; new
scanners drop in without touching the orchestrator.

Public surface:
    SecurityScannerService - runs the configured runners against
                             a workspace, returns a ``ScanReport``.
    ScannerProvider        - Protocol every runner satisfies.
    SecurityFinding        - DTO for a single finding.
    ScanReport             - DTO for the aggregated result.
    Severity               - finding-severity enum.
    SecurityScanBlocked    - raised when the report blocks task exec.
    RunnerUnavailableError - raised when a tool isn't installed
                             (warned, not blocked).
"""

from security_scanner_core_lib.security_scanner_core_lib.scanner_provider import (
    ScannerProvider,
)
from security_scanner_core_lib.security_scanner_core_lib.security_finding import (
    ScanReport,
    SecurityFinding,
    Severity,
)
from security_scanner_core_lib.security_scanner_core_lib.security_scanner_service import (
    SecurityScannerService,
    SecurityScanBlocked,
)
from security_scanner_core_lib.security_scanner_core_lib.runners._helpers import (
    RunnerUnavailableError,
)
