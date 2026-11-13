"""The shape every CLI agent transport has, module for module.

A CLI transport (a lib that drives one agent CLI) is a MIRROR of every other:
the same files, the same public names in each, the same function signatures,
with only the agent's name changed — ``CodexCliClient`` / ``GrokCliClient``,
``StreamingCodexSession`` / ``StreamingGrokSession``. A reader who has learned
one transport knows where everything is in the next, and a host can pick a
module by path (``<lib>.helpers.model_catalog``) instead of branching per
backend.

This module is that contract as data. A gate test walks every transport and
fails when one drifts: a file missing, a public name renamed, a parameter
dropped. ``{Agent}`` stands for the transport's class-name stem.

What a transport does INSIDE these names is its own: an agent with fewer
capabilities answers with an honest fallback (an empty list, a static set)
rather than leaving a name out.
"""

from __future__ import annotations

from agent_provider_contracts.agent_provider_contracts.agent_provider import (
    AgentProvider,
)
from agent_provider_contracts.agent_provider_contracts.agent_session import (
    AgentSession,
    AgentSessionEvent,
)
from agent_provider_contracts.agent_provider_contracts.agent_session_manager import (
    AgentSessionManager,
)

AGENT_PLACEHOLDER = '{Agent}'

#: Files every transport has, relative to the lib's root folder. ``<pkg>`` is
#: the package (``codex_core_lib`` inside ``codex_core_lib/``).
REQUIRED_FILES = (
    'README.md',
    '__init__.py',
    '<pkg>/__init__.py',
    '<pkg>/cli_client.py',
    '<pkg>/data/__init__.py',
    '<pkg>/helpers/__init__.py',
    '<pkg>/helpers/effort_levels.py',
    '<pkg>/helpers/model_catalog.py',
    '<pkg>/helpers/one_shot_utils.py',
    '<pkg>/session/__init__.py',
    '<pkg>/session/history.py',
    '<pkg>/session/index.py',
    '<pkg>/session/manager.py',
    '<pkg>/session/streaming.py',
    '<pkg>/session/wire_protocol.py',
    '<pkg>/tests/__init__.py',
    '<pkg>/tests/test_flow.py',
)

#: Module (dotted, relative to the package; '' = the package itself) → the
#: public names it must define or export.
REQUIRED_NAMES = {
    '': (
        '{Agent}CliClient', '{Agent}SessionManager',
        'Streaming{Agent}Session', 'SessionEvent',
    ),
    'cli_client': ('{Agent}CliClient',),
    'helpers.effort_levels': (
        'FALLBACK_EFFORT_LEVELS', 'discover_effort_levels', 'reset_effort_levels_cache',
    ),
    'helpers.model_catalog': (
        'FALLBACK_MODELS', 'discover_models', 'reset_models_cache', 'discover_pinned_models',
    ),
    'helpers.one_shot_utils': ('OneShotError', 'one_shot', 'make_one_shot'),
    'session.history': ('load_history_events',),
    'session.index': ('{Agent}SessionMetadata', 'default_sessions_root', 'list_sessions'),
    'session.manager': ('{Agent}SessionManager',),
    'session.streaming': ('Streaming{Agent}Session', 'SessionEvent'),
}

#: Function → its parameters, exactly and in order (``*`` = keyword-only after).
REQUIRED_SIGNATURES = {
    'helpers.effort_levels.discover_effort_levels': ('binary', 'timeout'),
    'helpers.effort_levels.reset_effort_levels_cache': (),
    'helpers.model_catalog.discover_models': ('force',),
    'helpers.model_catalog.reset_models_cache': (),
    'helpers.model_catalog.discover_pinned_models': ('force',),
    'helpers.one_shot_utils.one_shot': (
        'prompt', '*', 'binary', 'model', 'cwd', 'timeout_seconds',
    ),
    'helpers.one_shot_utils.make_one_shot': (
        '*', 'binary', 'model', 'cwd', 'timeout_seconds',
    ),
    'session.index.default_sessions_root': ('env',),
    'session.index.list_sessions': ('*', 'query', 'sessions_root', 'max_results'),
}

#: Function → parameters it must take, the first one first. Where the CLI
#: keeps its transcripts differs per agent, so the store-location argument is
#: the transport's own.
REQUIRED_PARAMETERS = {
    'session.history.load_history_events': ('agent_session_id', 'max_events'),
}

#: The fields a session-index row carries, so one picker draws any backend.
SESSION_METADATA_FIELDS = (
    'agent_session_id', 'cwd', 'last_modified_epoch', 'turn_count',
    'first_user_message', 'last_user_message',
)


def protocol_members(protocol: type) -> tuple[str, ...]:
    """The public names a Protocol declares, in declaration order."""
    return tuple(
        name for name in vars(protocol)
        if not name.startswith('_')
    )


#: Class → the members it must have (inherited ones count).
REQUIRED_MEMBERS = {
    '{Agent}CliClient': (*protocol_members(AgentProvider), 'investigate', 'model'),
    'Streaming{Agent}Session': (
        *protocol_members(AgentSession),
        'is_working', 'allowed_additional_dirs', 'git_access',
    ),
    '{Agent}SessionManager': protocol_members(AgentSessionManager),
    'SessionEvent': (*protocol_members(AgentSessionEvent), 'subtype'),
    '{Agent}SessionMetadata': (*SESSION_METADATA_FIELDS, 'to_dict'),
}


def for_agent(name: str, agent: str) -> str:
    """``name`` with the agent's class-name stem filled in."""
    return name.replace(AGENT_PLACEHOLDER, agent)
