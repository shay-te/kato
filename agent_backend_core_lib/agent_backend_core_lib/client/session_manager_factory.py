"""Which session-manager class runs each backend's chats.

A host keeps one manager per agent backend that has an interactive chat. The
persistent-process one (Claude) also owns the chat RECORDS for every backend;
the per-turn ones (Codex, Grok, OpenCode) own only their live sessions and
publish their records back into that one view. Every per-turn manager takes
the same four constructor arguments (``state_dir``, ``record_sink``,
``record_source``, ``git_access_source``), so a host builds each the same way.

This is the sanctioned lazy-import seam, the same as ``agent_client_factory``:
a transport's module tree is imported only when its chats are switched on.
"""

from __future__ import annotations

from importlib import import_module

from agent_backend_core_lib.agent_backend_core_lib.client.agent_client_factory import (
    resolve_platform,
)
from agent_backend_core_lib.agent_backend_core_lib.platform import AgentPlatform

#: Backends whose chats run one CLI process per turn, in tab order.
PER_TURN_CHAT_PLATFORMS = (
    AgentPlatform.CODEX,
    AgentPlatform.GROK,
    AgentPlatform.OPENCODE,
)

#: Every backend with an interactive chat, in tab order.
CHAT_PLATFORMS = (AgentPlatform.CLAUDE, *PER_TURN_CHAT_PLATFORMS)

_MANAGER_CLASSES = {
    AgentPlatform.CLAUDE: ('claude_core_lib.claude_core_lib.session.manager', 'ClaudeSessionManager'),
    AgentPlatform.CODEX: ('codex_core_lib.codex_core_lib.session.manager', 'CodexSessionManager'),
    AgentPlatform.GROK: ('grok_core_lib.grok_core_lib.session.manager', 'GrokSessionManager'),
    AgentPlatform.OPENCODE: (
        'opencode_core_lib.opencode_core_lib.session.manager', 'OpenCodeSessionManager',
    ),
}


def has_chat(backend: str) -> bool:
    """Does ``backend`` offer an interactive chat at all?"""
    try:
        return resolve_platform(backend) in CHAT_PLATFORMS
    except ValueError:
        return False


def session_manager_class(backend: str | AgentPlatform):
    """The manager class for ``backend``'s chats; ``ValueError`` when it has none."""
    platform = backend if isinstance(backend, AgentPlatform) else resolve_platform(backend)
    if platform not in _MANAGER_CLASSES:
        raise ValueError(f'agent backend {platform.value!r} has no interactive chat')
    module_path, class_name = _MANAGER_CLASSES[platform]
    return getattr(import_module(module_path), class_name)
