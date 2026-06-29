"""Extract the agent's plan markdown from a session's event stream.

In plan mode the agent never edits files — it presents its plan by
calling the ``ExitPlanMode`` tool. The plan text rides in that tool
call's ``input`` (the ``plan`` field). Older / alternate CLI shapes may
not carry the plan in the tool input, in which case the plan is the
assistant's own message text in the same turn, so we fall back to that.

Generic + product-agnostic: this reads the same ``recent_events()``
envelope shape (``event_type`` + ``raw.message.content`` blocks) that
``resume_prompt_utils`` reads, and returns a plain markdown string. The
caller decides where/whether to persist it.
"""
from __future__ import annotations

from agent_core_lib.agent_core_lib.helpers.text_utils import (
    normalized_text,
    text_from_mapping,
)

# The tool the agent calls to present its plan / leave plan mode. Matched
# case-insensitively so a CLI casing change can't silently break capture.
EXIT_PLAN_MODE_TOOL = 'ExitPlanMode'


def extract_plan_from_events(events) -> str:
    """Return the most recent plan markdown, or '' when there is none.

    Walks ``events`` newest-first and returns the plan from the first
    ``ExitPlanMode`` tool call found. The plan is read from the tool
    ``input`` (``plan`` field); if that is empty, it falls back to the
    flattened ``text`` blocks of the same assistant message.
    """
    for event in reversed(list(events or [])):
        raw = getattr(event, 'raw', None)
        content = _message_content(raw)
        if not isinstance(content, list):
            continue
        if not _content_has_exit_plan_mode(content):
            continue
        plan = _plan_text_from_content(content)
        if plan:
            return plan
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
