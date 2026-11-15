"""The agent a new task's chat starts on — the operator's pick.

``KATO_CHAT_AGENT``: ``claude`` | ``codex`` | ``grok`` | ``opencode``. Chosen on
quick start (whichever agent the operator uses and signs in to) and in Settings;
read fresh, so a change applies to the next task without a restart.

CHATS only. A ticket kato runs by itself still uses ``KATO_AGENT_BACKEND``
(Claude or OpenHands — the only backends that can run a task unattended), and
a task's chat stays on the agent it started with: the pick decides where a NEW
task's first chat begins, never moves one under way. Unset or unknown = the
configured agent's chat, as before.
"""

from __future__ import annotations

from kato_core_lib.helpers.kato_settings_store_utils import live_setting

CHAT_AGENT_KEY = 'KATO_CHAT_AGENT'
CHAT_AGENTS = ('claude', 'codex', 'grok', 'opencode')
DEFAULT_CHAT_AGENT = 'claude'


def normalized_chat_agent(value: object) -> str:
    """``value`` as a chat agent name, or '' when it is not one."""
    agent = str(value or '').strip().lower()
    return agent if agent in CHAT_AGENTS else ''


def chat_agent(env: dict | None = None) -> str:
    """The operator's pick for new tasks' chats ('' = none made)."""
    return normalized_chat_agent(live_setting(CHAT_AGENT_KEY, env))
