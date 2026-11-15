"""A new task's first chat starts on the operator's chosen agent.

``KATO_CHAT_AGENT`` (``chat_agent_utils``) names it. A chat is routed by the
agent recorded on it (``AgentSessionRouter.backend_for``), and a task with no
record yet goes to the configured default — Claude. So the record is written
FIRST, carrying the chosen agent, exactly as switching a task to another agent
does; the first message then spawns on that agent, its spawn defaults are that
agent's, and the UI shows that agent's tab from the start.

Only a task with no chat record yet: a chat under way is never moved. Only an
agent this kato has wired (its chat tab is enabled), and only when it is not
already the default — then there is nothing to write.
"""

from __future__ import annotations

from agent_core_lib.agent_core_lib.session.record import AgentSessionRecord


def start_on_chosen_agent(manager, task_id: str, agent: str, *, task_summary: str = '') -> bool:
    """Write ``task_id``'s first chat record on ``agent``; whether it did."""
    if not agent or manager is None:
        return False
    get_record = getattr(manager, 'get_record', None)
    save_record = getattr(manager, 'save_record', None)
    available = getattr(manager, 'available_backends', None)
    default = getattr(manager, 'default_backend', None)
    if not (callable(get_record) and callable(save_record) and callable(available)):
        return False
    if agent not in available() or (callable(default) and agent == default()):
        return False
    if get_record(task_id) is not None:
        return False
    save_record(AgentSessionRecord(
        task_id=str(task_id).strip(), task_summary=str(task_summary or ''), agent_backend=agent,
    ))
    return True
