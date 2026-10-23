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

A task the operator deletes takes its transcripts with it, so before the delete
its totals are kept (``snapshot_task_usage`` -> ``usage_history_store``,
numbers only) and reported apart as ``deleted_tasks``. ``totals`` stays what
exists now; ``totals_with_deleted`` adds the kept ones. Each task also carries
``created_at``; its last use is ``usage.last_at``.

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
from kato_core_lib.helpers.usage_history_store import deleted_task_usage, record_deleted_task_usage

_CLAUDE = AgentBackend.CLAUDE.value


def register_usage_routes(app: Flask, *, reader: TranscriptUsageReader | None = None) -> None:
    transcripts = reader or TranscriptUsageReader()
    # The delete route reads a task's usage through the SAME reader, so the
    # snapshot it keeps costs one cached read, not a second walk of the file.
    app.config['USAGE_READER'] = transcripts

    @app.get('/api/usage')
    def get_usage():
        return jsonify(collect_usage(
            app.config.get('SESSION_MANAGER'),
            app.config.get('WORKSPACE_MANAGER'),
            transcripts,
            logger=app.logger,
            deleted=deleted_task_usage(),
        ))


def collect_usage(session_manager, workspace_manager, reader: TranscriptUsageReader, *,
                  logger=None, deleted: list[dict] | None = None) -> dict:
    """Per-task token totals, biggest first, plus the grand total.

    ``deleted`` is what kato kept of tasks the operator has since deleted
    (``usage_history_store``): their transcripts are gone, so these figures are
    the only trace. They are reported APART from ``tasks`` — ``totals`` is
    what exists now, ``totals_with_deleted`` adds them — so the page can offer
    the history as a switch without ever mixing it into the live numbers.
    """
    workspaces = _workspaces_by_task(workspace_manager, logger)
    sessions = _records_by_task(session_manager, logger)

    tasks: list[dict] = []
    totals = empty_usage()
    uncounted: set[str] = set()
    claimed: set[str] = set()
    for key in sorted({*workspaces, *sessions}):
        entry = _task_entry(workspaces.get(key), sessions.get(key), reader, claimed)
        if entry is None:
            continue
        uncounted.update(entry['uncounted_backends'])
        add_usage(totals, entry['usage'])
        tasks.append(entry)
    tasks.sort(key=lambda task: (-task['usage']['total'], task['task_id']))
    deleted_tasks = [_kept_entry(entry) for entry in deleted or []]
    deleted_tasks.sort(key=lambda task: (-task['usage']['total'], task['task_id']))
    with_deleted = add_usage(empty_usage(), totals)
    for entry in deleted_tasks:
        add_usage(with_deleted, entry['usage'])
    _label_models(totals)
    _label_models(with_deleted)
    return {
        'tasks': tasks,
        'deleted_tasks': deleted_tasks,
        'totals': totals,
        'totals_with_deleted': with_deleted,
        'uncounted_backends': sorted(uncounted),
        # The date of the rates behind every ``cost`` figure, so the page can
        # say how old the estimate's prices are.
        'pricing_as_of': PRICES_AS_OF,
        'generated_at': _iso(time.time()),
    }


def snapshot_task_usage(app: Flask, task_id: str) -> dict | None:
    """What ``task_id`` has used so far, shaped to be kept after it is deleted.

    Call BEFORE the delete: it reads the transcripts the delete removes.
    ``None`` when the task used no tokens (nothing worth keeping) or the read
    fails — keeping a figure is never worth failing the delete over.
    """
    try:
        key = _key(task_id)
        reader = app.config.get('USAGE_READER') or TranscriptUsageReader()
        entry = _task_entry(
            _workspaces_by_task(app.config.get('WORKSPACE_MANAGER'), app.logger).get(key),
            _records_by_task(app.config.get('SESSION_MANAGER'), app.logger).get(key),
            reader, set(),
        )
    except Exception:
        app.logger.exception('usage: could not snapshot task %s before deleting it', task_id)
        return None
    if entry is None or entry['usage']['total'] <= 0:
        return None
    return {**entry, 'deleted_at': _iso(time.time())}


def keep_deleted_task_usage(app: Flask, entry: dict) -> None:
    """Persist a ``snapshot_task_usage`` result. Best effort, never raises."""
    try:
        record_deleted_task_usage(entry)
    except Exception:
        app.logger.exception('usage: could not keep the usage of deleted task %s', entry.get('task_id'))


def _task_entry(workspace, record, reader: TranscriptUsageReader, claimed: set[str]) -> dict | None:
    """One task's row — ``None`` when it owns no transcript not already counted."""
    # A transcript belongs to ONE task. The same file reachable from two
    # (an adopted chat, ids that differ only in case) would be counted in
    # both and inflate the grand total.
    paths = [path for path in _claude_transcripts(record, workspace) if path not in claimed]
    if not paths:
        return None
    claimed.update(paths)
    usage = empty_usage()
    for path in paths:
        add_usage(usage, reader.read(path))
    _label_models(usage)
    return {
        'task_id': _first_text(getattr(workspace, 'task_id', ''), getattr(record, 'task_id', '')),
        'summary': _first_text(
            getattr(workspace, 'task_summary', ''), getattr(record, 'task_summary', ''),
        ),
        'status': _first_text(getattr(workspace, 'status', '')),
        'created_at': _created_at(workspace, record, usage),
        'chats': len(paths),
        'uncounted_backends': _other_backends(record),
        'usage': usage,
    }


def _kept_entry(entry: dict) -> dict:
    """A kept snapshot read back from disk, made safe for the page.

    The totals are rebuilt through ``add_usage`` so a hand-edited or older
    file still yields every field the page reads; the models are named again
    so a renamed label shows its current name, as for a live task.
    """
    usage = add_usage(empty_usage(), entry.get('usage') or {})
    _label_models(usage)
    return {
        'task_id': str(entry.get('task_id') or '').strip(),
        'summary': str(entry.get('summary') or '').strip(),
        'status': str(entry.get('status') or '').strip(),
        'created_at': str(entry.get('created_at') or '').strip(),
        'deleted_at': str(entry.get('deleted_at') or '').strip(),
        'chats': int(entry.get('chats') or 0),
        'uncounted_backends': [str(name) for name in entry.get('uncounted_backends') or []],
        'usage': usage,
    }


def _created_at(workspace, record, usage: dict) -> str:
    """When the task was created: the earliest stamp kato kept, else its first turn."""
    stamps = [
        epoch for epoch in (
            _epoch(getattr(workspace, 'created_at_epoch', 0)),
            _epoch(getattr(record, 'created_at_epoch', 0)),
        ) if epoch > 0
    ]
    return _iso(min(stamps)) if stamps else str(usage.get('first_at') or '')


def _epoch(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _iso(epoch: float) -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(epoch))


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
