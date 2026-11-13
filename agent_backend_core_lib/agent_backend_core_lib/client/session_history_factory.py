"""Read a chat's past turns back from the store of the CLI that wrote them.

A live chat's events live in its session object's memory, so a chat survives
a page reload but not a restart of the process hosting it. Each CLI already
writes every turn to disk, and each transport's ``session/history.py`` reads
its own store back as events in that transport's live wire shape — the shape
the chat renders through its one path.

Which reader to use is a routing question, so it lives here beside the other
factories, with the same lazy import: a host configured for one backend never
imports another's module tree to replay a chat.
"""

from __future__ import annotations

from importlib import import_module

from agent_backend_core_lib.agent_backend_core_lib.client.agent_client_factory import (
    resolve_platform,
)
from agent_backend_core_lib.agent_backend_core_lib.platform import AgentPlatform

_HISTORY_MODULES = {
    AgentPlatform.CLAUDE: 'claude_core_lib.claude_core_lib.session.history',
    AgentPlatform.CODEX: 'codex_core_lib.codex_core_lib.session.history',
    AgentPlatform.GROK: 'grok_core_lib.grok_core_lib.session.history',
    AgentPlatform.OPENCODE: 'opencode_core_lib.opencode_core_lib.session.history',
}


def load_history_events(backend: str, agent_session_id: str) -> list[dict]:
    """The chat's turns from ``backend``'s own store, or ``[]``.

    Never raises: an unknown backend, a missing store, an unreadable file all
    answer ``[]``. A chat that renders empty is better than one that fails to
    open.
    """
    session_id = str(agent_session_id or '').strip()
    if not session_id:
        return []
    try:
        module = import_module(_HISTORY_MODULES[resolve_platform(backend)])
        return list(module.load_history_events(session_id))
    except Exception:
        return []
