"""Kato planning UI server.

This package will host a small Flask app whose job is to render one tab per
in-flight Kato task and stream the Claude Code CLI session bound to that task
so a human can intervene (chat, answer permission asks, refine the plan).

The current revision is a skeleton: it exposes the basic Flask app, a no-op
session manager, and an HTML page placeholder. The full streaming and
ticket-state-driven tab lifecycle will be wired in subsequent iterations.
"""

from kato_webserver.app import create_app

__all__ = ["create_app"]
