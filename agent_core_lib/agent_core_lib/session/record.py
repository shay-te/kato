"""What the orchestrator remembers about one agent chat, between turns.

A chat outlives its subprocess: the process sleeps, the host restarts, the
operator comes back tomorrow and expects the tab, its history, and its cost
reading to still be there. This record is that memory, and none of it is
specific to one CLI — the id field is already the cross-backend
``agent_session_id``, and every other field is something the orchestrator or
the UI needs regardless of who is answering.

Kept deliberately small: only what is needed to rehydrate a tab. The
transcript lives in the CLI's own storage.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from agent_core_lib.agent_core_lib.helpers.session_id_utils import (
    fix_session_id,
    read_session_id_from_mapping,
)
from utils_core_lib.utils_core_lib.text_utils import text_from_mapping

SESSION_STATUS_ACTIVE = 'active'
SESSION_STATUS_DONE = 'done'
SESSION_STATUS_REVIEW = 'review'
SESSION_STATUS_TERMINATED = 'terminated'

SUPPORTED_SESSION_STATUSES = frozenset({
    SESSION_STATUS_ACTIVE,
    SESSION_STATUS_DONE,
    SESSION_STATUS_REVIEW,
    SESSION_STATUS_TERMINATED,
})


def _non_negative_int(value) -> int:
    """``int(value)`` clamped at 0; 0 for anything unparseable."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0

def session_id_list(value) -> list:
    """Normalize a raw payload value into a clean, de-duplicated id list."""
    ids: list = []
    for item in (value if isinstance(value, list) else []):
        fixed = fix_session_id(item)
        if fixed and fixed not in ids:
            ids.append(fixed)
    return ids

def _chats_by_backend(value) -> dict:
    """Normalise the per-backend chat map read from disk.

    Tolerant by design: this is operator-visible state that a hand-edit or an
    older host may have written differently, and a malformed entry must cost
    that backend's history — not the whole record, and not the task's tab.
    """
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    for backend, entry in value.items():
        key = str(backend or '').strip().lower()
        if not key or not isinstance(entry, dict):
            continue
        out[key] = {
            'agent_session_id': fix_session_id(entry.get('agent_session_id')),
            'previous_session_ids': session_id_list(
                entry.get('previous_session_ids'),
            ),
        }
    return out


@dataclass
class AgentSessionRecord(object):
    """On-disk metadata for one agent chat session, whatever the backend.

    Stored as JSON at ``<state_dir>/<task_id>.json``. The live subprocess is
    NOT part of this record — only what is needed to rehydrate and display the
    tab after a restart. The conversation transcript itself lives in the CLI's
    own session storage and is rejoined by that CLI's resume mechanism, which
    is why nothing here is transport-shaped: every field is something the
    orchestrator and the UI need, not something one CLI happens to provide.
    """

    task_id: str
    task_summary: str = ''
    # The agent's session id for this task. ``agent_session_id`` is
    # the canonical name across every the orchestrator agent backend (Claude,
    # Codex, OpenHands, ...).
    agent_session_id: str = ''
    # Last context-window reading reported by a live turn. Persisted because
    # the live figure lives on the subprocess object: once a session sleeps or
    # the host restarts, there is nothing to read and the composer's indicator
    # vanished entirely. The number is still only WRITTEN by a live assistant
    # turn — this just lets the last known value outlive the subprocess.
    context_used_tokens: int = 0
    context_model: str = ''
    # What THIS chat cost on its first measured turn — the floor a fresh
    # chat would start from (system prompt + project instructions + any
    # injected docs). Every later turn re-reads the whole context, so
    # ``context_used_tokens / context_baseline_tokens`` is what a session
    # costs relative to starting over, which is the number that tells an
    # operator when to open a new chat. Reset by ``start_new_chat``.
    context_baseline_tokens: int = 0
    # Which agent backend produced this chat ('claude', 'codex', ...).
    # Recorded per CHAT, not read from current config, because the operator
    # can switch backends between chats and an old conversation still belongs
    # to the CLI that wrote it: resuming it means resuming THAT CLI, and the
    # UI must not label yesterday's Claude chat with today's setting.
    # Empty on records written before this field existed — callers treat that
    # as "unknown", never as a default backend.
    agent_backend: str = ''
    status: str = SESSION_STATUS_ACTIVE
    created_at_epoch: float = field(default_factory=time.time)
    updated_at_epoch: float = field(default_factory=time.time)
    cwd: str = ''
    # The branch the orchestrator prepared for this task. The webserver compares this
    # against the repo's HEAD before forwarding any message to the live
    # subprocess; if they diverge (the orchestrator has moved on to a different task)
    # the send is rejected. Empty string disables the check (wait-planning
    # tabs that aren't owned by the orchestrator).
    expected_branch: str = ''
    # Earlier chats for this task, oldest first. ``start_new_chat`` pushes
    # the detached session id here so the operator can navigate back to an
    # old conversation (each id resumes via the normal --resume path).
    previous_session_ids: list = field(default_factory=list)
    # Per-backend chat history: ``{backend: {'agent_session_id': str,
    # 'previous_session_ids': [...]}}``.
    #
    # A task can hold a live conversation with each backend at once — they
    # are different agents, and an operator asking Codex something does not
    # want their Claude thread replaced. The fields ABOVE mirror whichever
    # backend is currently active, so every existing reader keeps working
    # unchanged; this map is what makes the inactive ones recoverable.
    # Empty on records written before per-backend chats existed.
    chats_by_backend: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> 'AgentSessionRecord':
        return cls(
            task_id=text_from_mapping(payload, 'task_id'),
            task_summary=str(payload.get('task_summary', '') or ''),
            agent_session_id=read_session_id_from_mapping(payload),
            agent_backend=str(payload.get('agent_backend', '') or ''),
            status=str(payload.get('status', SESSION_STATUS_ACTIVE) or SESSION_STATUS_ACTIVE),
            created_at_epoch=float(payload.get('created_at_epoch', time.time()) or time.time()),
            updated_at_epoch=float(payload.get('updated_at_epoch', time.time()) or time.time()),
            cwd=text_from_mapping(payload, 'cwd'),
            expected_branch=str(payload.get('expected_branch', '') or ''),
            previous_session_ids=session_id_list(payload.get('previous_session_ids')),
            chats_by_backend=_chats_by_backend(payload.get('chats_by_backend')),
            # Restored, not defaulted: ``to_dict`` has always written these,
            # but this reader dropped them, so the reading the docstring
            # promises would "outlive the subprocess" actually died on the
            # next load and the indicator blanked after every restart.
            context_used_tokens=_non_negative_int(payload.get('context_used_tokens')),
            context_model=str(payload.get('context_model', '') or ''),
            context_baseline_tokens=_non_negative_int(
                payload.get('context_baseline_tokens'),
            ),
        )
