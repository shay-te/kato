"""DTO for the per-task context kato prepares before calling an agent.

Kato's preflight produces this for every task — branch name,
repository scope, workspace cwd, branch-name-per-repo mapping. Both
existing backends (Claude CLI, OpenHands) consume it as an opaque
context object; the structural fields are listed here so impls can
type their accessors without depending on kato.

This is intentionally loose. Each backend reads only what it needs;
unset fields default to empty so the contract package can construct
a stub for tests without pulling kato's prepare logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentPreparedTaskContext(object):
    branch_name: str = ''
    branches_by_repository: dict[str, str] = field(default_factory=dict)
    repositories: list[Any] = field(default_factory=list)
    cwd: str = ''
