"""DTO for a task handed to an agent backend.

Minimal subset of fields the existing two backends (Claude CLI,
OpenHands) actually consume: id, summary, description,
repositories. Kato's richer ``Task`` model translates to this at
the call boundary so the contract package stays free of any
``kato_core_lib`` dependency.

``repositories`` is intentionally typed as ``list[Any]`` — each
backend reads only the fields it needs (``id``, ``remote_url``,
``local_path``) via attribute access. Pinning a stricter type here
would require pulling kato's Repository data class into the
contracts package, which would re-introduce the dependency this
DTO exists to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentTask(object):
    id: str = ''
    summary: str = ''
    description: str = ''
    repositories: list[Any] = field(default_factory=list)
