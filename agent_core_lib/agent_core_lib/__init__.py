"""Agent-backend factory.

Mirrors ``task_core_lib`` and ``repository_core_lib``: thin
factory wrapper that picks the configured backend (Claude /
OpenHands / future Codex) and exposes it through the shared
``agent_provider_contracts.AgentProvider`` interface.

Public surface:
    AgentCoreLib    - composition root.
    AgentPlatform   - enum of supported backends.
"""

from agent_core_lib.agent_core_lib.agent_core_lib import AgentCoreLib
from agent_core_lib.agent_core_lib.platform import AgentPlatform
