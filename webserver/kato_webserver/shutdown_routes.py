"""Shut kato down from the UI — the very path Ctrl+C takes.

``GET  /api/shutdown`` → ``{running}``: the tasks whose agents are working now,
                         which kato resumes when it starts again.
``POST /api/shutdown`` → 202 ``{running, shutting_down}``; then kato stops: it
                         records the working agents, stops every agent and
                         watcher, and exits (``main._register_shutdown_hook``).

The stop is the Ctrl+C handler itself, raised on kato's own process half a
second after the answer left — one graceful path, never a second one to keep
in step.
"""

from __future__ import annotations

import signal
import threading

from flask import Flask, jsonify

from kato_core_lib.data_layers.service.restart_resume.running_agents import running_task_ids

#: Long enough for the 202 to reach the browser before kato goes.
ANSWER_GRACE_SECONDS = 0.5


def press_ctrl_c() -> None:
    """Kato's own SIGINT: its handler runs on the main thread, as for Ctrl+C."""
    signal.raise_signal(signal.SIGINT)


def register_shutdown_routes(app: Flask) -> None:
    def running() -> list[str]:
        return running_task_ids(app.config.get('SESSION_MANAGER'))

    @app.get('/api/shutdown')
    def shutdown_preview():
        return jsonify({'running': running()})

    @app.post('/api/shutdown')
    def shut_down():
        working = running()
        timer = threading.Timer(ANSWER_GRACE_SECONDS, app.config.get('REQUEST_SHUTDOWN') or press_ctrl_c)
        timer.daemon = True
        timer.start()
        return jsonify({'running': working, 'shutting_down': True}), 202
