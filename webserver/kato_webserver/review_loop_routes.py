"""HTTP routes for a task's review loop.

    GET  /api/sessions/<task>/review-loop                         the loop, every round
    POST /api/sessions/<task>/review-loop                         start one (202 / 400 / 409)
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


def register_review_loop_routes(app: Flask, *, collect_diffs: Callable[[str], list]) -> None:
    """``collect_diffs(task_id)`` reads every repository's diff, read-only."""

    def review_loops():
        return getattr(app.config.get('AGENT_SERVICE'), 'review_loops', None)

    def unavailable():
        return jsonify({'error': 'review loops are not available until kato is configured'}), 503

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
        summary, description = _task_text(app, task_id)
        try:
            state = loops.start(
                task_id,
                diff_source=collect_diffs,
                task_summary=summary,
                task_description=description,
                max_rounds=max_rounds,
                **stages,
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
_STAGES = ('self_check', 'verify_tests', 'confirm_clean', 'extra_sweep')


def _requested_stages(body: object) -> dict[str, bool]:
    source = body if isinstance(body, dict) else {}
    return {name: source.get(name) is not False for name in _STAGES}


def _task_text(app: Flask, task_id: str) -> tuple[str, str]:
    """The ticket's summary and description, from the task's workspace record."""
    workspace_manager = app.config.get('WORKSPACE_MANAGER')
    if workspace_manager is None:
        return '', ''
    try:
        record = workspace_manager.get(task_id)
    except Exception:
        return '', ''
    return (
        str(getattr(record, 'task_summary', '') or ''),
        str(getattr(record, 'task_description', '') or ''),
    )
