"""A task's Docker containers, from its header: list, stop, remove.

``GET    /api/sessions/<task>/containers``         → ``{available, containers, reason}``
``POST   /api/sessions/<task>/containers/stop``    → what was stopped, and what failed
``POST   /api/sessions/<task>/containers/remove``  → what was removed (with its Compose
                                                     networks and volumes), and what failed

Never polled: read when the task's header shows and after each action.
"""

from __future__ import annotations

from flask import Flask, jsonify

from kato_core_lib.data_layers.service.task_containers import TaskContainers


def task_containers_of(app: Flask) -> TaskContainers:
    """The app's one ``TaskContainers`` (built on first use from its workspaces)."""
    containers = app.config.get('TASK_CONTAINERS')
    if containers is None:
        # The manager itself (set once, in ``create_app``): a function here
        # would be handed back as-is by ``provider_for``, never called.
        containers = TaskContainers(workspace_manager=app.config.get('WORKSPACE_MANAGER'))
        app.config['TASK_CONTAINERS'] = containers
    return containers


def register_task_container_routes(app: Flask) -> None:
    @app.get('/api/sessions/<task_id>/containers')
    def list_task_containers(task_id: str):
        return jsonify(task_containers_of(app).of_task(task_id))

    @app.post('/api/sessions/<task_id>/containers/<action>')
    def act_on_task_containers(task_id: str, action: str):
        if action not in ('stop', 'remove'):
            return jsonify({'error': f'unknown action {action!r}'}), 404
        outcome = getattr(task_containers_of(app), action)(task_id)
        return jsonify(outcome.to_dict()), 200 if outcome.ok else 502
