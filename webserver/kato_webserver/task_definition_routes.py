"""HTTP route for a task's own definition — the title and description it was
created with.

    GET /api/sessions/<task>/task-definition   {task_id, summary, description}
                                               (404: no workspace for the task)

What the agent was given as its task: the workspace record, written when the
task was provisioned (a ticket's description, or what was typed in New task) —
the same record the chat's Task definition is built from. Read when the
operator opens it, never polled: the 5s tab list leaves the description out on
purpose.
"""

from __future__ import annotations

from flask import Flask, jsonify


def register_task_definition_routes(app: Flask) -> None:

    @app.get('/api/sessions/<task_id>/task-definition')
    def get_task_definition(task_id: str):
        manager = app.config.get('WORKSPACE_MANAGER')
        try:
            workspace = manager.get(task_id) if manager is not None else None
        except Exception:
            workspace = None
        if workspace is None:
            return jsonify({'error': f'no workspace for task {task_id}'}), 404
        return jsonify({
            'task_id': task_id,
            'summary': str(getattr(workspace, 'task_summary', '') or ''),
            'description': str(getattr(workspace, 'task_description', '') or ''),
        })
