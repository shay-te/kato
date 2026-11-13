"""HTTP routes for handing a task over to a teammate (the header's "Hand over").

    GET  /api/sessions/<task>/handover        {available, reason, remove_here_reason}
         Cheap — no tracker call: may this task be handed over at all? (A task
         created in kato, or a tracker that cannot, cannot.) ``remove_here_reason``
         ('' = it may) is why the picker's "also delete it from my tasks" is
         not offered: deleting a plan's source here would take the plan's
         sub-tasks with it (``driven_plan``). The delete itself is the tab's
         own (``DELETE …/workspace``), run by the UI once the hand-over landed.
    GET  /api/sessions/<task>/handover/users  {users: [{handle, name}]}
         Who the ticket can be assigned to — asked of the tracker when the
         picker opens. 400 when it cannot be handed over, 502 when the tracker
         did not answer.
    POST /api/sessions/<task>/handover        {handle, name, wait_for_them} -> {status, landed, assignee, steps}
         ``TaskHandover.hand_over``: assign + verify, tag kato:wait-editing
         (``wait_for_them: false`` — only the literal false — takes it off
         instead: their kato starts on it by itself; anything else, or nothing,
         tags it), clear kato's blocking comments, post who has it, move to Open, stop
         the agent here (``task_agent_stop`` — the chat's Stop). 409 while the
         agent is mid-turn; 400 for a task that cannot be handed over; 502 when
         the assignment itself failed (nothing else was done).
"""

from __future__ import annotations

from flask import Flask, jsonify, request

from kato_core_lib.data_layers.service.task_handover import (
    HandoverUnavailableError,
    TaskBusyError,
)
from kato_webserver.driven_plan import plan_driven_from
from kato_webserver.task_agent_stop import stop_task_agent

STARTING_REASON = 'kato is still starting'
PLAN_SOURCE_REASON = (
    'a plan was split from this task here: deleting it would delete the plan\'s sub-tasks too '
    'and decline their pull requests'
)


def register_handover_routes(app: Flask) -> None:

    @app.get('/api/sessions/<task_id>/handover')
    def handover_availability(task_id: str):
        handover = _handover(app)
        reason = handover.unavailable_reason(task_id) if handover is not None else STARTING_REASON
        return jsonify({
            'available': not reason,
            'reason': reason,
            'remove_here_reason': PLAN_SOURCE_REASON if plan_driven_from(app, task_id) is not None else '',
        })

    @app.get('/api/sessions/<task_id>/handover/users')
    def handover_users(task_id: str):
        handover = _handover(app)
        if handover is None:
            return jsonify({'error': STARTING_REASON}), 503
        try:
            users = handover.users(task_id)
        except HandoverUnavailableError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception as exc:
            app.logger.exception('could not list who %s can be handed to', task_id)
            return jsonify({'error': f'the tracker did not say who the ticket can go to: {exc}'}), 502
        return jsonify({'users': users})

    @app.post('/api/sessions/<task_id>/handover')
    def hand_over(task_id: str):
        handover = _handover(app)
        if handover is None:
            return jsonify({'error': STARTING_REASON}), 503
        body = request.get_json(silent=True) or {}
        try:
            result = handover.hand_over(
                task_id,
                str(body.get('handle') or ''),
                str(body.get('name') or ''),
                stop_agent=lambda task: stop_task_agent(
                    app, task, reason='the task was handed over', source='webserver_handover_route',
                ),
                # Only an explicit ``false`` lets their kato run it unwatched.
                wait_for_them=body.get('wait_for_them') is not False,
            )
        except TaskBusyError as exc:
            return jsonify({'error': str(exc)}), 409
        except ValueError as exc:  # also HandoverUnavailableError
            return jsonify({'error': str(exc)}), 400
        return jsonify(result), (502 if result['status'] == 'failed' else 200)


def _handover(app: Flask):
    return getattr(app.config.get('AGENT_SERVICE'), 'handover', None)
