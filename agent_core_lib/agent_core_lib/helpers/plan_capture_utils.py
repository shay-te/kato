"""Extract the agent's plan markdown from a session's event stream.

In plan mode the agent presents its plan by calling the ``ExitPlanMode``
tool — the plan text rides in that tool call's ``input`` (the ``plan``
field). Older / alternate CLI shapes may not carry the plan in the tool
input, in which case the plan is the assistant's own message text in the
same turn, so we fall back to that. NEWER CLIs instead persist the
finalized plan by WRITING it to a plans file (e.g.
``~/.claude/plans/<slug>.md``) — so when there is no usable
``ExitPlanMode`` plan we also read the plan from a write targeting a
``…/plans/….md`` path (otherwise the plan is created but never surfaced
in the host UI — the "made the plan but never shows it" report).

Generic + product-agnostic: this reads the same ``recent_events()``
envelope shape (``event_type`` + ``raw.message.content`` blocks) that
``resume_prompt_utils`` reads, and returns a plain markdown string. The
caller decides where/whether to persist it.
"""
from __future__ import annotations

from utils_core_lib.utils_core_lib.text_utils import (
    normalized_text,
    text_from_mapping,
)

# The tool the agent calls to present its plan / leave plan mode. Matched
# case-insensitively so a CLI casing change can't silently break capture.
EXIT_PLAN_MODE_TOOL = 'ExitPlanMode'

# File-writing tools a CLI might use to persist the finalized plan.
_PLAN_FILE_WRITE_TOOLS = frozenset({'write', 'create_file', 'create_new_file'})
# The path fragment that marks a plans file — matched after normalizing
# ``\`` → ``/`` so a Windows ``…\.claude\plans\x.md`` path is caught too.
_PLAN_PATH_MARKER = '/plans/'


def extract_plan_from_events(events) -> str:
    """Return the most recent plan markdown, or '' when there is none.

    Walks ``events`` newest-first and, per assistant message, prefers the
    ``ExitPlanMode`` plan (its ``input.plan``, else the message's flattened
    ``text``) and otherwise reads a plan written to a ``…/plans/….md`` file.
    Returns the first non-empty plan found.
    """
    for event in reversed(list(events or [])):
        raw = getattr(event, 'raw', None)
        content = _message_content(raw)
        if not isinstance(content, list):
            continue
        plan = _plan_from_content(content)
        if plan:
            return plan
    return ''


def _plan_from_content(content: list) -> str:
    """The plan for ONE assistant message: ExitPlanMode first (it's the
    intentional presentation), then a plans-file write as the fallback for
    CLIs that persist the plan to disk instead of the tool input."""
    if _content_has_exit_plan_mode(content):
        plan = _plan_text_from_content(content)
        if plan:
            return plan
    return _plan_from_plans_file_write(content)


def _plan_from_plans_file_write(content: list) -> str:
    """Plan markdown from a write-tool call targeting a ``…/plans/….md``
    path (``~/.claude/plans/<slug>.md``), or '' when there's no such write."""
    for block in content:
        if not isinstance(block, dict) or block.get('type') != 'tool_use':
            continue
        if normalized_text(block.get('name')).lower() not in _PLAN_FILE_WRITE_TOOLS:
            continue
        tool_input = block.get('input')
        raw_path = (
            text_from_mapping(tool_input, 'file_path')
            or text_from_mapping(tool_input, 'path')
        )
        path = normalized_text(raw_path).replace('\\', '/').lower()
        if _PLAN_PATH_MARKER not in path or not path.endswith('.md'):
            continue
        body = text_from_mapping(tool_input, 'content')
        if body:
            return body
    return ''


def _content_has_exit_plan_mode(content: list) -> bool:
    return any(_is_exit_plan_mode_block(block) for block in content)


def _is_exit_plan_mode_block(block) -> bool:
    if not isinstance(block, dict) or block.get('type') != 'tool_use':
        return False
    name = normalized_text(block.get('name')).lower()
    return name == EXIT_PLAN_MODE_TOOL.lower()


def _plan_text_from_content(content: list) -> str:
    """Prefer the ``ExitPlanMode`` tool input's ``plan``; fall back to the
    assistant's own ``text`` blocks in the same message."""
    for block in content:
        if not _is_exit_plan_mode_block(block):
            continue
        plan = text_from_mapping(block.get('input'), 'plan')
        if plan:
            return plan
    return _flatten_text_blocks(content)


def _message_content(raw):
    message = raw.get('message') if isinstance(raw, dict) else None
    if not isinstance(message, dict):
        return None
    return message.get('content')


def _flatten_text_blocks(content) -> str:
    if not isinstance(content, list):
        return ''
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get('type') == 'text':
            text = text_from_mapping(block, 'text')
            if text:
                parts.append(text)
    return '\n\n'.join(parts).strip()
