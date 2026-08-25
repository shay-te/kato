"""Switching a task between its per-backend chats.

A task can hold a conversation with each backend at once. They are different
agents: asking Codex something must not replace the Claude thread the operator
was in the middle of, and coming back to the Claude tab must find that thread
exactly where they left it.

The record stores the ACTIVE backend's chat in its top-level fields —
``agent_backend`` / ``agent_session_id`` / ``previous_session_ids`` — so every
existing reader keeps working without knowing this exists. The inactive
backends live in ``chats_by_backend``. Switching tabs is therefore a swap:
park the active chat in the map, lift the target's out of it.

The swap is the only place that writes both halves, so it is the only place
they can disagree. Everything else reads the top-level fields.
"""

from __future__ import annotations

from agent_core_lib.agent_core_lib.session.record import AgentSessionRecord


def parked_chat(record: AgentSessionRecord, backend: str) -> dict:
    """What ``backend``'s chat looks like right now, active or parked.

    An EMPTY ``backend`` means "whatever chat this record holds" and returns
    the top-level fields. That is the case for every record written before
    backends were tracked — asking those for a named backend's chat would
    answer "none", and the operator's existing conversation would vanish from
    the list.
    """
    key = _key(backend)
    if not key or key == _key(getattr(record, 'agent_backend', '')):
        return {
            'agent_session_id': str(getattr(record, 'agent_session_id', '') or ''),
            'previous_session_ids': list(
                getattr(record, 'previous_session_ids', []) or [],
            ),
        }
    entry = (getattr(record, 'chats_by_backend', None) or {}).get(key) or {}
    return {
        'agent_session_id': str(entry.get('agent_session_id', '') or ''),
        'previous_session_ids': list(entry.get('previous_session_ids') or []),
    }


def switch_backend(record: AgentSessionRecord, backend: str) -> AgentSessionRecord:
    """Make ``backend`` the active chat, parking the one it replaces.

    A no-op when it is already active — re-parking would be harmless but
    would rewrite the record on every tab render.

    Mutates and returns ``record``; the caller persists it. Nothing is lost:
    the outgoing chat's id and history go into the map, and an operator who
    switches back finds the same conversation.
    """
    target = _key(backend)
    if not target:
        return record
    current = _key(getattr(record, 'agent_backend', ''))
    if current == target:
        return record
    chats = dict(getattr(record, 'chats_by_backend', None) or {})
    if current:
        chats[current] = {
            'agent_session_id': str(getattr(record, 'agent_session_id', '') or ''),
            'previous_session_ids': list(
                getattr(record, 'previous_session_ids', []) or [],
            ),
        }
    incoming = chats.pop(target, None) or {}
    record.agent_backend = target
    record.agent_session_id = str(incoming.get('agent_session_id', '') or '')
    record.previous_session_ids = list(incoming.get('previous_session_ids') or [])
    record.chats_by_backend = chats
    return record


def backends_with_chats(record: AgentSessionRecord) -> list[str]:
    """Every backend this task has a chat under, active one first."""
    active = _key(getattr(record, 'agent_backend', ''))
    others = sorted(
        key for key in (getattr(record, 'chats_by_backend', None) or {})
        if key and key != active
    )
    return ([active] if active else []) + others


def _key(value: object) -> str:
    return str(value or '').strip().lower()
