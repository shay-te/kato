"""Write the agent's captured plan into a task workspace as ``plan.md``.

Owns only the Kato-specific persistence: the filename + workspace layout
(``<workspace>/plan.md``) and the atomic write. The generic extraction of
the plan from a session's events lives in
``agent_core_lib.agent_core_lib.helpers.plan_capture_utils``; the cadence +
lifecycle live in ``ResumePromptWatcher`` (which captures the plan on the
same tick it refreshes ``resume_prompt.md``).
"""
from __future__ import annotations

from pathlib import Path

from kato_core_lib.helpers.atomic_text_utils import atomic_write_text
from kato_core_lib.helpers.logging_utils import configure_logger

PLAN_FILENAME = 'plan.md'


def write_plan(
    workspace_path: Path | str,
    content: str,
    *,
    logger=None,
) -> bool:
    """Write ``content`` to ``<workspace>/plan.md`` atomically.

    Returns True on success, False on empty content or any I/O failure.
    An empty plan is never written (it would clobber a real plan with a
    blank file on a turn that produced no ExitPlanMode call).
    """
    if not workspace_path or not str(content or '').strip():
        return False
    target = Path(str(workspace_path)) / PLAN_FILENAME
    return atomic_write_text(
        target,
        content,
        logger=logger or configure_logger(__name__),
        label='plan.md',
    )
