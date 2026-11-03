"""Answer remembered permissions on the server, with no browser open.

An "Allow always" or a timed grant is the operator's standing answer, but it
was only ever applied when a browser asked: the pending poll, the session list,
a chat's stream. The UI stops polling while its page is hidden, so an agent
that hit a permission already granted sat waiting until the page came back,
which read as "the review loop only runs when I focus the tab". With the lid
closed there is no browser at all, so agents would stall on their first such
tool call.

This runs the same check on a timer. It decides nothing new: every carve-out
(a question for the operator, a write outside the task folder, leaving plan
mode, a high-risk Action Guard verdict) lives in the one path it calls, and an
ask nothing there answers still waits for a person.
"""

from __future__ import annotations

from flask import Flask
from utils_core_lib.utils_core_lib.tick_thread import TickThread

from kato_webserver.app import _pending_permission_tool_by_task

TICK_SECONDS = 2.0


def resolve_remembered_permissions(app: Flask) -> None:
    with app.app_context():
        _pending_permission_tool_by_task(app.config.get('SESSION_MANAGER'), app=app)


def build_and_start_permission_auto_resolver(
    app: Flask,
    *,
    tick_seconds: float = TICK_SECONDS,
    autostart: bool = True,
) -> TickThread:
    resolver = TickThread(
        lambda: resolve_remembered_permissions(app),
        interval_seconds=tick_seconds,
        name='permission-auto-resolver',
        logger=app.logger,
    )
    if autostart:
        resolver.start()
    return resolver
