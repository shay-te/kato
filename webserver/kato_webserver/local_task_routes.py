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

from typing import Callable

from flask import Flask, jsonify, request

from kato_core_lib.data_layers.service.local_task_service import (
    START_MODES,
    START_PLAN,
    LocalTaskRejected,
)
from kato_webserver.local_task_starter import LocalTaskStarter


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
        body = request.get_json(silent=True) or {}
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
            )
        except LocalTaskRejected as exc:
            status = 403 if exc.unapproved else 400
            return jsonify({'error': str(exc), 'unapproved_repositories': exc.unapproved}), status
        task_id = str(task.id)
        starter.start(task_id, start_mode, model=str(body.get('model') or ''), effort=effort)
        return jsonify({
            'task_id': task_id, 'summary': task.summary, 'start_mode': start_mode,
        }), 202
