"""Enum of agent backends ``agent_backend_core_lib`` knows how to construct.

Mirrors the shape of ``task_core_lib.platform.Platform`` and
``repository_core_lib.platform.Platform`` so a reader who has
already learned one factory pattern recognises this one. Aliases
on the lookup side (``claude-code``, ``open-hands``, …) are
kept inside the factory's ``from_config_string`` helper rather
than duplicated as enum members.
"""

from __future__ import annotations

from enum import Enum

from agent_core_lib.agent_core_lib.data.agent_backend import AgentBackend


class AgentPlatform(Enum):
    """Agent backends supported by ``agent_backend_core_lib``.

    Values are DERIVED from ``AgentBackend`` rather than restated: the same
    strings are persisted in session records and compared in libs that cannot
    import this one, and two hand-maintained copies of a name is how a backend
    check starts silently failing.
    """

    CLAUDE = AgentBackend.CLAUDE.value
    CODEX = AgentBackend.CODEX.value
    OPENHANDS = AgentBackend.OPENHANDS.value
