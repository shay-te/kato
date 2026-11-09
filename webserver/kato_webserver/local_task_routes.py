"""``POST /api/local-tasks`` — create a task in kato itself (no tracker) and start it.

The "New task" tab posts here. Inside the request, everything that can refuse
the task is checked and the task is recorded, so the answer is either a clear
refusal or ``202 {task_id}`` — and by then the task's tab exists ("provisioning"
on the tab list's next poll). The slow part runs in the background: clone the
repositories and check out the branch (its progress is the tab's preparation
log), then start the chat in the chosen mode — Plan unless the operator picked
otherwise. "Just open the chat" stops after the clone and sends nothing.

The model / effort / mode are applied BEFORE the chat starts: the CLI fixes
``--model``, ``--effort`` and ``--permission-mode`` when it spawns.
"""

from __future__ import annotations

import json
from typing import Callable

from flask import Flask, jsonify, request

from kato_core_lib.data_layers.service.local_task_service import (
    START_MODES,
    START_PLAN,
    LocalTaskRejected,
)
from kato_webserver.local_task_starter import LocalTaskStarter
from kato_webserver.prompt_attachment_store import save_attachment


def register_local_task_routes(
    app: Flask,
    *,
    set_mode: Callable[[str, str], bool],
    set_override: Callable[[str, str, str], bool],
    effort_levels: Callable[[], list[str]],
    start_chat: Callable[[str, str], object],
    run_in_background: Callable[[Callable[[], None], str], None] | None = None,
) -> None:
    starter = LocalTaskStarter(
        app, set_mode=set_mode, set_override=set_override,
        start_chat=start_chat, run_in_background=run_in_background,
    )
    # The plan queue starts its local steps through the same starter.
    app.config['LOCAL_TASK_STARTER'] = starter

    def _service():
        return getattr(app.config.get('AGENT_SERVICE'), 'local_tasks', None)

    @app.post('/api/local-tasks')
    def create_local_task():
        service = _service()
        if service is None:
            return jsonify({'error': 'creating tasks in kato is not available'}), 503
        body, attachments = _creation_request_body(request)
        start_mode = str(body.get('start_mode') or START_PLAN).strip()
        if start_mode not in START_MODES:
            return jsonify({
                'error': f'unknown start mode {start_mode!r}', 'allowed': list(START_MODES),
            }), 400
        effort = str(body.get('effort') or '').strip().lower()
        if effort and effort not in effort_levels():
            return jsonify({'error': f'unknown effort {effort!r}'}), 400
        repositories = body.get('repositories') or []
        if not isinstance(repositories, list):
            return jsonify({'error': 'repositories must be a list'}), 400
        try:
            task = service.create(
                str(body.get('summary') or ''),
                str(body.get('description') or ''),
                [str(item) for item in repositories],
                branch=str(body.get('branch') or ''),
            )
        except LocalTaskRejected as exc:
            return jsonify({'error': str(exc)}), 400
        task_id = str(task.id)
        if attachments:
            # Saved NOW, synchronously, before the background clone+spawn
            # thread starts — so by the time it builds the opening message
            # (after the clone), these files are already on disk and named
            # in it. No race against the upload.
            _save_created_task_attachments(app, task_id, attachments)
        starter.start(task_id, start_mode, model=str(body.get('model') or ''), effort=effort)
        return jsonify({
            'task_id': task_id, 'summary': task.summary, 'start_mode': start_mode,
        }), 202


def _creation_request_body(req) -> tuple[dict, list]:
    """``(body, attachments)`` from either a plain JSON POST or a multipart
    one carrying files: ``payload`` (the same JSON, stringified) plus zero
    or more ``attachments`` fields. Multipart only when there is at least
    one file to attach, so the common case is unchanged.
    """
    uploads = req.files.getlist('attachments')
    if not uploads:
        return req.get_json(silent=True) or {}, []
    try:
        body = json.loads(req.form.get('payload') or '{}')
    except (TypeError, ValueError):
        body = {}
    return (body if isinstance(body, dict) else {}), uploads


def _save_created_task_attachments(app: Flask, task_id: str, uploads) -> None:
    """Best-effort: a file that fails to save costs the agent that one
    document, never the task — the create response has already gone out.
    """
    workspace_manager = app.config.get('WORKSPACE_MANAGER')
    if workspace_manager is None:
        return
    try:
        workspace_dir = str(workspace_manager.workspace_path(task_id))
    except Exception:
        app.logger.exception('no workspace to save attachments for task %s', task_id)
        return
    for upload in uploads:
        if not upload or not upload.filename:
            continue
        result = save_attachment(workspace_dir, upload.filename, upload.read())
        if not result.get('ok'):
            app.logger.warning(
                'could not save attachment %s for task %s: %s',
                upload.filename, task_id, result.get('error'),
            )
