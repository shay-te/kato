"""The issue-platform value that means "no tracker at all".

``KATO_ISSUE_PLATFORM=local``: every task is one the operator created in
kato's own UI (``LOCAL-<n>``, ``local_task_core_lib``). Nothing is polled and
no tracker keys are needed — so first-run setup must not demand any.

Kept in its own module because ``validate_env`` (a standalone check run before
kato boots), the service wiring and the webserver all need the same name.
"""

from __future__ import annotations

NO_TRACKER_ISSUE_PLATFORM = 'local'


def is_no_tracker(issue_platform: object) -> bool:
    return str(issue_platform or '').strip().lower() == NO_TRACKER_ISSUE_PLATFORM
