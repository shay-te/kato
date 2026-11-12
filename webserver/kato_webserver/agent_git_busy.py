"""Kato's git buttons wait while the agent may be running git in the same clone.

With strict git switched off (``agent_git_access_utils``), a chat's agent
commits, rebases and resets in the task's clones during its turn. A Push, Pull,
Merge master, Update source, Pull request or Done pressed at that moment runs
kato's git in the same repository: two writers on one index and one HEAD, and a
push of whatever half-finished state the branch is in at that instant (mid-
rebase, HEAD is not even on the branch).

So those buttons answer 409 until the turn ends. Only a session that was
actually spawned at local access counts (``session.git_access`` — what the CLI
was started with, not what Settings says now): in a strict session the agent
never moves HEAD or makes a commit, which is why pressing Push mid-turn has
always been allowed there and still is.
"""

from __future__ import annotations

from agent_core_lib.agent_core_lib.helpers.command_floor import GIT_ACCESS_LOCAL

AGENT_GIT_BUSY_MESSAGE = (
    'the agent is working and may be running git in this task\'s clones '
    '(strict git is off). Press it again once its turn ends.'
)


def agent_git_busy_reason(manager, task_id: str) -> str:
    """Why a kato git action must wait right now, or ``''`` when it may run."""
    if manager is None:
        return ''
    try:
        session = manager.get_session(task_id)
    except Exception:  # noqa: BLE001 - a lookup failure must not block a button
        return ''
    if session is None or not getattr(session, 'is_alive', False):
        return ''
    if str(getattr(session, 'git_access', '') or '') != GIT_ACCESS_LOCAL:
        return ''
    if not getattr(session, 'is_working', False):
        return ''
    return AGENT_GIT_BUSY_MESSAGE
