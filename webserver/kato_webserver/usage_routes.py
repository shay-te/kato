"""``GET /api/usage`` — how many tokens each task has consumed, and what that is worth.

Each task's ``usage`` carries token counts AND an estimated API-equivalent
``cost``, priced per message at its own model's published rates
(``claude_core_lib.helpers.model_pricing``), split per model with each model's
first and last use — so a task that switched model shows both. A model with no
price on file is listed in ``unpriced_models``, never counted as free.

Read from the agent's own transcripts, not from anything kato counted itself:
the transcript is the one record that survives a restart, a lazily-resumed
chat, and a chat the operator set aside, and it already holds every API call.
A task's figure is the sum over ALL the Claude chats it has had — the current
one plus each earlier one kept in ``previous_session_ids`` — because "what did
this task cost" is not "what did its open tab cost".

Only Claude transcripts are read. A task that also has chats with another
agent says so (``uncounted_backends``) rather than quietly reporting a total
that leaves them out.

The expensive part — walking transcripts that reach hundreds of megabytes — is
:class:`TranscriptUsageReader`, which remembers how far it has read, so only
the first request after a start pays for a full read.
"""

from __future__ import annotations

import time

from flask import Flask, jsonify

from agent_core_lib.agent_core_lib.data.agent_backend import AgentBackend
from agent_core_lib.agent_core_lib.session.backend_chats import backends_with_chats, parked_chat
from claude_core_lib.claude_core_lib.helpers.model_pricing import PRICES_AS_OF
from claude_core_lib.claude_core_lib.helpers.transcript_usage import (
    TranscriptUsageReader,
    add_usage,
    empty_usage,
)
from claude_core_lib.claude_core_lib.session.history import find_session_file
from kato_core_lib.helpers.model_label_utils import model_display_label

_CLAUDE = AgentBackend.CLAUDE.value


def register_usage_routes(app: Flask, *, reader: TranscriptUsageReader | None = None) -> None:
    transcripts = reader or TranscriptUsageReader()

    @app.get('/api/usage')
    def get_usage():
        return jsonify(collect_usage(
            app.config.get('SESSION_MANAGER'),
            app.config.get('WORKSPACE_MANAGER'),
            transcripts,
            logger=app.logger,
        ))


def collect_usage(session_manager, workspace_manager, reader: TranscriptUsageReader, *,
                  logger=None) -> dict:
    """Per-task token totals, biggest first, plus the grand total."""
    workspaces = _workspaces_by_task(workspace_manager, logger)
    sessions = _records_by_task(session_manager, logger)

    tasks: list[dict] = []
    totals = empty_usage()
    uncounted: set[str] = set()
    claimed: set[str] = set()
    for key in sorted({*workspaces, *sessions}):
        workspace = workspaces.get(key)
        record = sessions.get(key)
        # A transcript belongs to ONE task. The same file reachable from two
        # (an adopted chat, ids that differ only in case) would be counted in
        # both and inflate the grand total.
        paths = [path for path in _claude_transcripts(record, workspace) if path not in claimed]
        if not paths:
            continue
        claimed.update(paths)
        usage = empty_usage()
        for path in paths:
            add_usage(usage, reader.read(path))
        other_agents = _other_backends(record)
        uncounted.update(other_agents)
        add_usage(totals, usage)
        _label_models(usage)
        tasks.append({
            'task_id': _first_text(
                getattr(workspace, 'task_id', ''), getattr(record, 'task_id', ''),
            ),
            'summary': _first_text(
                getattr(workspace, 'task_summary', ''), getattr(record, 'task_summary', ''),
            ),
            'status': _first_text(getattr(workspace, 'status', '')),
            'chats': len(paths),
            'uncounted_backends': other_agents,
            'usage': usage,
        })
    tasks.sort(key=lambda task: (-task['usage']['total'], task['task_id']))
    _label_models(totals)
    return {
        'tasks': tasks,
        'totals': totals,
        'uncounted_backends': sorted(uncounted),
        # The date of the rates behind every ``cost`` figure, so the page can
        # say how old the estimate's prices are.
        'pricing_as_of': PRICES_AS_OF,
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }


def _label_models(usage: dict) -> None:
    """Give each model a name a person reads (``claude-opus-5-5`` -> ``Opus 5.5``).

    Named here, once, so the page never formats a model id itself and a task's
    row, its chips and its table can't name the same model two ways.
    """
    for model, slot in usage['models'].items():
        slot['label'] = model_display_label(model) or 'unknown'


def _claude_transcripts(record, workspace) -> list[str]:
    """Every distinct Claude transcript file this task owns, oldest chat first."""
    ids: list[str] = []
    if record is not None:
        ids.extend(_claude_session_ids(record))
    # The workspace metadata keeps the id of the chat it last ran, which a
    # session record can lack (a task adopted, or a record lost in a restart).
    if workspace is not None:
        ids.append(str(getattr(workspace, 'agent_session_id', '') or ''))
    paths: list[str] = []
    for session_id in ids:
        found = find_session_file(session_id) if session_id else None
        if found is not None and str(found) not in paths:
            paths.append(str(found))
    return paths


def _claude_session_ids(record) -> list[str]:
    """Claude's chat ids on ``record``: earlier ones first, the current last."""
    chat = _claude_chat(record)
    return [*chat['previous_session_ids'], chat['agent_session_id']]


def _claude_chat(record) -> dict:
    # A record written before backends were tracked has an EMPTY backend and
    # its top-level chat is Claude's; ``parked_chat`` only knows that for ''.
    active = str(getattr(record, 'agent_backend', '') or '').strip().lower()
    return parked_chat(record, '' if active in ('', _CLAUDE) else _CLAUDE)


def _other_backends(record) -> list[str]:
    """Agents other than Claude that hold a chat on this task."""
    if record is None:
        return []
    return [
        backend for backend in backends_with_chats(record)
        if backend != _CLAUDE and _has_chat(parked_chat(record, backend))
    ]


def _has_chat(chat: dict) -> bool:
    return bool(chat['agent_session_id'] or chat['previous_session_ids'])


def _first_text(*values) -> str:
    for value in values:
        text = str(value or '').strip()
        if text:
            return text
    return ''


def _records_by_task(session_manager, logger) -> dict:
    if session_manager is None:
        return {}
    try:
        records = session_manager.list_records()
    except Exception:
        if logger is not None:
            logger.exception('usage: could not list the session records')
        return {}
    return {_key(record.task_id): record for record in records or [] if getattr(record, 'task_id', '')}


def _workspaces_by_task(workspace_manager, logger) -> dict:
    if workspace_manager is None:
        return {}
    try:
        workspaces = workspace_manager.list_workspaces()
    except Exception:
        if logger is not None:
            logger.exception('usage: could not list the workspaces')
        return {}
    return {_key(ws.task_id): ws for ws in workspaces or [] if getattr(ws, 'task_id', '')}


def _key(task_id) -> str:
    # The tracker and the UI do not always agree on a task id's case.
    return str(task_id).strip().lower()
