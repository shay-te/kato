"""Is any of kato's agents working? One waiting for the operator is not."""

from __future__ import annotations


def agents_are_working(session_manager) -> bool:
    for record in session_manager.list_records():
        session = session_manager.get_session(record.task_id)
        if session is None or not session.is_working:
            continue
        if not session.pending_control_request_tool():
            return True
    return False
