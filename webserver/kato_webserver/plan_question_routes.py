"""HTTP routes for a plan sub-task's question to its plan's chat.

    GET    /api/sessions/<task>/plan-question   the task's plan parent ('' = none) and its question, if any
    POST   /api/sessions/<task>/plan-question   ask the parent's chat {questions: [AskUserQuestion questions]}
                                                 (202 / 400 / 404 no plan knows the task / 409 already asking)
    DELETE /api/sessions/<task>/plan-question   stop waiting for the answer

The asking is ``kato_core_lib.data_layers.service.plan_queue.parent_question``.
The answer is shown beside the question; the operator still answers it.
"""

from __future__ import annotations

from flask import Flask, jsonify, request

from kato_core_lib.data_layers.service.plan_queue.parent_question import ParentQuestionError

_STATUS_BY_KIND = {'not_found': 404, 'conflict': 409, 'invalid': 400}


def register_plan_question_routes(app: Flask) -> None:

    def questions():
        return getattr(app.config.get('AGENT_SERVICE'), 'plan_questions', None)

    def unavailable():
        return jsonify({'error': 'plan questions are not available until kato is configured'}), 503

    @app.get('/api/sessions/<task_id>/plan-question')
    def get_plan_question(task_id: str):
        service = questions()
        if service is None:
            return unavailable()
        return jsonify({'parent': service.parent_of(task_id), 'question': service.state(task_id)})

    @app.post('/api/sessions/<task_id>/plan-question')
    def ask_plan_question(task_id: str):
        service = questions()
        if service is None:
            return unavailable()
        body = request.get_json(silent=True) or {}
        try:
            state = service.ask(task_id, body.get('questions') if isinstance(body, dict) else None)
        except ParentQuestionError as exc:
            return jsonify({'error': str(exc)}), _STATUS_BY_KIND.get(exc.kind, 409)
        return jsonify({'parent': state['parent'], 'question': state}), 202

    @app.delete('/api/sessions/<task_id>/plan-question')
    def cancel_plan_question(task_id: str):
        service = questions()
        if service is None:
            return unavailable()
        return jsonify({'question': service.cancel(task_id)})
