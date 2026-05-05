"""The Protocol every scanner runner implements.

The five runners (``detect_secrets``, ``bandit``, ``safety``,
``npm_audit``, ``env_file``) all already match this shape via duck
typing — this Protocol formalises what was previously a docstring
contract in ``runners/__init__.py``.

Why ``Protocol`` and not ``ABC``: the existing runners are plain
modules with a ``run`` function, not classes. Making them inherit
from a base class would force a class-wrapper layer with no value.
The runtime-checkable Protocol lets a new runner opt in just by
matching the function signature on a module.

Runners must:

- Catch their own tool's exceptions and either return ``[]`` or
  raise ``RunnerUnavailableError`` (the orchestrator surfaces
  those as warnings, not blockers — missing tool ≠ security issue).
- Resolve all paths relative to ``workspace_path`` and never
  traverse outside it.
- Skip ``.git/``, ``node_modules/``, ``.venv/``, and the standard
  ``EXCLUDE_DIRS`` set defined in ``runners/_helpers.py``.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from security_scanner_core_lib.security_scanner_core_lib.security_finding import (
    SecurityFinding,
)


@runtime_checkable
class ScannerProvider(Protocol):
    def run(
        self,
        workspace_path: str,
        logger: logging.Logger | None = None,
        timeout_seconds: float | None = None,
    ) -> list[SecurityFinding]:
        """Walk ``workspace_path`` and return a list of findings.

        ``timeout_seconds`` is advisory — runners are also wrapped
        in a ``ThreadPoolExecutor`` timeout by the orchestrator,
        so a runner ignoring this kwarg is correct (just less
        granular). Returning ``[]`` means "scan succeeded, found
        nothing"; raising ``RunnerUnavailableError`` means "tool
        not installed, please don't block on me".
        """
        ...
