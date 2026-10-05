"""Write the agent's captured plan into a task workspace as ``plan.md``.

Owns only the Kato-specific persistence: the filename + workspace layout
(``<workspace>/plan.md``) and the atomic write. The generic extraction of
the plan from a session's events lives in
``agent_core_lib.agent_core_lib.helpers.plan_capture_utils``; the cadence +
lifecycle live in ``ResumePromptWatcher`` (which captures the plan on the
same tick it refreshes ``resume_prompt.md``).

``plan.md`` has TWO writers: kato, when the agent presents a plan, and the
agent itself, ticking the plan's progress checklist as it works (and
creating the file once when its plan never reached kato — see
``PLAN_PROGRESS_GUIDANCE``). The capture marker tells a new plan from a
ticked box — it moves only when a new plan lands, so the UI can auto-open a
NEW plan without springing open on every ticked box.
"""
from __future__ import annotations

from pathlib import Path

from kato_core_lib.helpers.atomic_text_utils import atomic_write_text
from kato_core_lib.helpers.logging_utils import configure_logger

PLAN_FILENAME = 'plan.md'

# ``plan.md``'s mtime (ns) as of kato's last capture. The file's own mtime
# also advances whenever the agent ticks a checkbox, so it cannot answer
# "is this a new plan?" on its own.
PLAN_CAPTURED_MARKER_FILENAME = '.kato-plan-captured'


# Appended to the system prompt of every streaming spawn (new, resumed,
# respawned after a restart) — a first message does not survive a resume or a
# summarised conversation, and the operator wants EVERY plan to be trackable.
#
# Path-agnostic on purpose: the task-folder boundary at the top of the same
# system prompt names the folder, and that folder is exactly where the
# watcher writes ``plan.md`` — so one static string serves every spawn path.
PLAN_PROGRESS_GUIDANCE = (
    '# Plans end with a progress checklist\n'
    '\n'
    'Every plan you present (plan mode / ExitPlanMode) ends with this '
    'section, and nothing comes after it:\n'
    '\n'
    '## Progress\n'
    '- [ ] <first milestone — concrete and checkable>\n'
    '- [ ] <next milestone>\n'
    '\n'
    'One checkbox per milestone, in the order you will do them. Put the whole '
    'plan in the message you present it with.\n'
    '\n'
    'You never save the plan yourself. Kato saves the plan you present as '
    f'`{PLAN_FILENAME}` directly inside your task folder (the folder named in '
    'the task-folder boundary above), and that copy is the one the operator '
    'watches. Never rewrite or overwrite that file, and never write a plans '
    'file anywhere else (such as under `~/.claude/plans/`, which is outside '
    'your task folder).\n'
    '\n'
    f'Once the plan is approved, read `{PLAN_FILENAME}` before you start. If '
    'it is missing, the plan never reached kato: create it once, with the '
    'full plan you presented and its Progress section, and from then on only '
    'tick it.\n'
    '\n'
    'While you carry the plan out:\n'
    '- Work milestone by milestone. When one is done and verified, tick it '
    f'before you start the next: read `{PLAN_FILENAME}`, then edit only that '
    'line from `- [ ]` to `- [x]`. One targeted edit per milestone, never a '
    'blanket replace and never a rewrite of the file.\n'
    '- Tick only finished work; a half-done milestone stays `- [ ]`.\n'
    '- If the work changes, add, drop or reword checklist lines in '
    f'`{PLAN_FILENAME}` (targeted edits again) so the list always matches '
    'what you are actually doing.\n'
    '- A revised plan you present keeps the ticks of the milestones already '
    'done.'
)


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
    logger = logger or configure_logger(__name__)
    workspace = Path(str(workspace_path))
    target = workspace / PLAN_FILENAME
    if not atomic_write_text(target, content, logger=logger, label='plan.md'):
        return False
    _mark_captured(workspace, target, logger)
    return True


def plan_captured_mtime(workspace_path: Path | str) -> int:
    """``plan.md``'s mtime (ns) at kato's last capture, or 0 when unknown.

    0 means "no marker" — a plan written before the marker existed, or a
    marker that could not be written. Callers fall back to the file's own
    mtime, which for such a plan only ever moved on a capture anyway.
    """
    if not workspace_path:
        return 0
    marker = Path(str(workspace_path)) / PLAN_CAPTURED_MARKER_FILENAME
    try:
        return int(marker.read_text(encoding='utf-8').strip() or 0)
    except (OSError, ValueError):
        return 0


def adopt_uncaptured_plan(workspace_path: Path | str, *, logger=None) -> bool:
    """Give a ``plan.md`` kato did not capture its capture marker, once.

    The agent writes ``plan.md`` itself when its plan never reached kato (a
    CLI that presents an empty ``ExitPlanMode`` and keeps the plan in a file
    outside the task folder). Without a marker the UI falls back to the
    file's mtime — and every box the agent ticks would then look like a new
    plan and pop the pane open. Marking it at first sight pins "new plan" to
    when the file appeared. Returns True when a marker was written.
    """
    if not workspace_path:
        return False
    workspace = Path(str(workspace_path))
    target = workspace / PLAN_FILENAME
    if (workspace / PLAN_CAPTURED_MARKER_FILENAME).exists() or not target.is_file():
        return False
    _mark_captured(workspace, target, logger or configure_logger(__name__))
    return (workspace / PLAN_CAPTURED_MARKER_FILENAME).exists()


def _mark_captured(workspace: Path, target: Path, logger) -> None:
    marker = workspace / PLAN_CAPTURED_MARKER_FILENAME
    try:
        captured = str(target.stat().st_mtime_ns)
    except OSError:
        captured = ''
    if captured and atomic_write_text(
        marker, captured, logger=logger, label='plan capture marker',
    ):
        return
    # A stale marker would hide this new plan from the auto-open; no marker
    # falls back to the file's mtime, which still shows it.
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        pass
