"""Which tasks' agents are mid-turn right now."""

from __future__ import annotations


def running_task_ids(session_manager) -> list[str]:
    """The tasks whose live chat is working — or waiting for the operator's
    approval inside its turn (it was running, and stopping it cuts that turn
    short too). Sorted, each once."""
    if session_manager is None:
        return []
    try:
        records = session_manager.list_records()
    except Exception:
        return []
    found: set[str] = set()
    for record in records:
        task_id = str(getattr(record, 'task_id', '') or '').strip()
        if task_id and is_mid_turn(session_manager, task_id):
            found.add(task_id)
    return sorted(found)


def is_mid_turn(session_manager, task_id: str) -> bool:
    """Is ``task_id``'s live chat working, or waiting for the operator's
    approval inside its turn? False when it cannot be told."""
    if session_manager is None:
        return False
    try:
        session = session_manager.get_session(task_id)
        if session is None or not getattr(session, 'is_alive', False):
            return False
        if getattr(session, 'is_working', False):
            return True
        waiting = getattr(session, 'pending_control_request_tool', None)
        return bool(waiting()) if callable(waiting) else False
    except Exception:
        return False
