"""Stop everything kato runs for one task — THE one place, for every route that does.

The chat's Stop and "Hand over" both end a task's work here: its review loop
first (even when the chat has no session yet — the loop may be mid-review),
then its agent, then the ``stop`` hook, fired only after the kill went through
so its observers (audit log, Slack mirror) see stops that really happened.
"""

from __future__ import annotations

from flask import Flask

STOPPED = 'stopped'
NO_AGENT = 'no agent was running'


def stop_task_agent(app: Flask, task_id: str, *, reason: str, source: str) -> str:
    """``STOPPED``, or ``NO_AGENT`` when the task had no session. Raises when
    the agent could not be stopped."""
    loops = getattr(app.config.get('AGENT_SERVICE'), 'review_loops', None)
    if loops is not None:
        try:
            loops.stop(task_id, reason=reason)
        except Exception:
            app.logger.exception('failed to stop the review loop for %s', task_id)
    manager = app.config['SESSION_MANAGER']
    if manager.get_record(task_id) is None:
        return NO_AGENT
    manager.terminate_session(task_id)
    fire_webserver_hook(app, 'stop', {'task_id': task_id, 'source': source})
    return STOPPED


def fire_webserver_hook(app: Flask, point: str, event: dict) -> None:
    """Fire a configured hook from a webserver route.

    Routes don't import :mod:`kato_core_lib.hooks` directly so the
    webserver can boot without that package installed (test
    environments, embedded use). Lazy-import + isolate failures.
    """
    runner = app.config.get('HOOK_RUNNER')
    if runner is None:
        return
    try:
        from kato_core_lib.hooks.config import HookPoint
        runner.fire(HookPoint(point), dict(event))
    except Exception:
        app.logger.exception('webserver hook firing failed for %s', point)
