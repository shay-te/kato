from __future__ import annotations

from openhands_agent.app_core_lib import app


def run() -> list[dict[str, str]]:
    return app.process_assigned_tasks()
