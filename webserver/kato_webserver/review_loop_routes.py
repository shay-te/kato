"""HTTP routes for a task's review loop.

    GET  /api/sessions/<task>/review-loop                         the loop, every round
    POST /api/sessions/<task>/review-loop                         start one (202 / 400 / 409)
    POST /api/sessions/<task>/review-loop/resume                  pick the last one up (202 / 409)
    POST /api/sessions/<task>/review-loop/stop                    stop it (200 / 404)
    GET  /api/sessions/<task>/review-loop/<loop>/rounds/<n>/<kind>  one round's text

The cheap per-task summary (where the loop is right now) rides the tab list's
5-second poll as ``review_loop``; these routes serve the view that opens on it.
The loop itself is ``review_loop_core_lib``; kato's wiring is
``kato_core_lib.data_layers.service.review_loop_adapters``.
"""

from __future__ import annotations

from typing import Callable

from flask import Flask, jsonify, request

from kato_core_lib.helpers.model_label_utils import model_display_label
from kato_core_lib.helpers.plan_writer import read_task_plan
from kato_webserver.model_request_utils import requested_model


def register_review_loop_routes(app: Flask, *, collect_diffs: Callable[[str], list]) -> None:
    """``collect_diffs(task_id)`` reads every repository's diff, read-only."""

    def review_loops():
        return getattr(app.config.get('AGENT_SERVICE'), 'review_loops', None)

    def unavailable():
        return jsonify({'error': 'review loops are not available until kato is configured'}), 503

    # What a finished task tagged ``kato:review-loop`` calls once it parks. In
    # setup mode the agent service does not exist yet, so main hands this over
    # when it does (``_mark_webserver_configured``).
    def start_on_park(task_id: str, rounds: int) -> None:
        start_task_review_loop(app, task_id, collect_diffs=collect_diffs, max_rounds=rounds)

    app.config['START_REVIEW_LOOP_ON_PARK'] = start_on_park
    if app.config.get('AGENT_SERVICE') is not None:
        app.config['AGENT_SERVICE'].parked_task_listener = start_on_park

    # The model a review runs on when the operator picks none — the picker
    # shows it as the concrete choice it is, never as "Default".
    @app.get('/api/review-loop/default-model')
    def review_loop_default_model():
        model = str(getattr(app.config.get('AGENT_SERVICE'), 'review_loop_default_model', '') or '')
        return jsonify({'model': model, 'label': model_display_label(model)})

    @app.get('/api/sessions/<task_id>/review-loop')
    def get_review_loop(task_id: str):
        loops = review_loops()
        if loops is None:
            return unavailable()
        state = loops.state(task_id)
        return jsonify({'loop': state.to_dict() if state is not None else None})

    @app.post('/api/sessions/<task_id>/review-loop')
    def start_review_loop(task_id: str):
        from review_loop_core_lib.review_loop_core_lib.service import ReviewLoopError

        loops = review_loops()
        if loops is None:
            return unavailable()
        body = request.get_json(silent=True)
        max_rounds, problem = _requested_rounds(body)
        if problem:
            return jsonify({'error': problem}), 400
        stages = _requested_stages(body)
        model, problem = requested_model(body)
        if problem:
            return jsonify({'error': problem}), 400
        try:
            state = start_task_review_loop(
                app,
                task_id,
                collect_diffs=collect_diffs,
                max_rounds=max_rounds,
                model=model,
                stages=stages,
            )
        except ReviewLoopError as exc:
            return jsonify({'error': str(exc)}), 409
        return jsonify({'loop': state.to_dict()}), 202

    # The task's latest loop, picked up where it was cut off (stopped, failed,
    # interrupted or stuck) — with its own rounds, stages, model and decisions.
    # Nothing is read from the body: a resume continues the loop as it was.
    @app.post('/api/sessions/<task_id>/review-loop/resume')
    def resume_review_loop(task_id: str):
        from review_loop_core_lib.review_loop_core_lib.service import ReviewLoopError

        loops = review_loops()
        if loops is None:
            return unavailable()
        summary, description = _task_text(app, task_id)
        try:
            state = loops.resume(
                task_id,
                diff_source=collect_diffs,
                task_summary=summary,
                task_description=description,
            )
        except ReviewLoopError as exc:
            return jsonify({'error': str(exc)}), 409
        return jsonify({'loop': state.to_dict()}), 202

    @app.post('/api/sessions/<task_id>/review-loop/stop')
    def stop_review_loop(task_id: str):
        loops = review_loops()
        if loops is None:
            return unavailable()
        if not loops.stop(task_id):
            return jsonify({'error': 'no review loop is running for this task'}), 404
        return jsonify({'stopped': True})

    @app.get('/api/sessions/<task_id>/review-loop/<loop_id>/rounds/<int:round_number>/<kind>')
    def get_review_loop_artifact(task_id: str, loop_id: str, round_number: int, kind: str):
        loops = review_loops()
        if loops is None:
            return unavailable()
        text = loops.artifact(task_id, loop_id, round_number, kind)
        if text is None:
            return jsonify({'error': 'no such round or artifact'}), 404
        return jsonify({'text': text})


def start_task_review_loop(
    app: Flask,
    task_id: str,
    *,
    collect_diffs: Callable[[str], list],
    max_rounds: int | None = None,
    model: str = '',
    stages: dict[str, bool] | None = None,
):
    """Start a loop held to the task's own text: the button and the park step both come here.

    ``stages`` left out means the same defaults the view's checkboxes start at.
    Raises ``ReviewLoopError`` when the loop refuses to start.
    """
    summary, description = _task_text(app, task_id)
    return app.config['AGENT_SERVICE'].review_loops.start(
        task_id,
        diff_source=collect_diffs,
        task_summary=summary,
        task_description=description,
        max_rounds=max_rounds,
        model=model,
        **_with_test_policy(_STAGE_DEFAULTS if stages is None else stages),
    )


def _requested_rounds(body: object) -> tuple[int | None, str]:
    """The operator's round limit from the start request, or None for the default.

    Refused rather than clamped: a limit outside the range is a client bug, and
    silently running a different number of rounds than the operator picked
    would hide it.
    """
    from review_loop_core_lib.review_loop_core_lib.runner import MAX_ROUNDS_LIMIT

    value = body.get('max_rounds') if isinstance(body, dict) else None
    if value is None:
        return None, ''
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_ROUNDS_LIMIT:
        return None, f'max_rounds must be a whole number from 1 to {MAX_ROUNDS_LIMIT}'
    return value, ''


# The loop's optional stages. ON unless the request turns one off: a
# self-check in the main chat first (cheap — it holds the context), tests that
# must pass before "clean", a clean-room reviewer confirming a clean verdict
# that came from a reviewer who saw the fix ping-pong, and one more clean-room
# sweep after ANY clean verdict ("it was clean, we ran it again, and it found a
# MAJOR" — one clean review is not proof).
# What a loop runs when the request leaves a stage out — the same defaults the
# view's checkboxes start at (reviewLoopStagesPref.js; a test pins the two
# together). The self-check in the main chat is the operator's opt-in.
_STAGE_DEFAULTS = {
    'self_check': False,
    'verify_tests': True,
    'confirm_clean': True,
    'extra_sweep': True,
}


def _with_test_policy(stages: dict[str, bool]) -> dict[str, bool]:
    """No test stage while "Run tests" is off (Settings → Automation): the
    operator said kato runs no tests in a task, the loop's included."""
    from kato_core_lib.helpers.automation_switches_utils import run_tests_enabled

    if run_tests_enabled():
        return dict(stages)
    return {**stages, 'verify_tests': False}


def _requested_stages(body: object) -> dict[str, bool]:
    """An explicit true / false is the operator's pick; anything else, the default."""
    source = body if isinstance(body, dict) else {}
    return {
        name: source[name] if isinstance(source.get(name), bool) else default
        for name, default in _STAGE_DEFAULTS.items()
    }


def _task_text(app: Flask, task_id: str) -> tuple[str, str]:
    """What the reviewer holds the change to: the ticket's summary and
    description, from the task's workspace record, then the task's plan. The
    plan is often the only real statement of the task (UNA-3343's whole ticket
    was a title), and it says what was decided NOT to do."""
    workspace_manager = app.config.get('WORKSPACE_MANAGER')
    if workspace_manager is None:
        return '', ''
    try:
        record = workspace_manager.get(task_id)
    except Exception:
        return '', ''
    description = str(getattr(record, 'task_description', '') or '')
    plan = read_task_plan(workspace_manager, task_id)
    return (
        str(getattr(record, 'task_summary', '') or ''),
        f'{description}\n\n## The plan\n{plan}'.strip() if plan else description,
    )
