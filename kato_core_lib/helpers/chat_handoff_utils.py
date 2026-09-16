"""Start a new chat from a summary of the one before it.

When a chat's context window runs low, the operator can ask for a fresh chat
that begins with a summary of the old one. The agent writes that summary itself,
in one turn, between the markers below; kato lifts it out of the transcript and
hands it to the new chat as its first message. The markers are what make the
summary unmistakable — no earlier assistant text can be taken for it.

The prompt that asks for it lives in the UI
(``webserver/ui/src/predefined_prompts/handoff_summary.md``) and must spell the
markers exactly as they are here; ``tests/test_chat_handoff_utils.py`` holds the
two to each other.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

from agent_core_lib.agent_core_lib.helpers.resume_prompt_utils import (
    build_inputs_from_session,
)

HANDOFF_OPEN = '<kato-handoff>'
HANDOFF_CLOSE = '</kato-handoff>'

_HANDOFF_BLOCK = re.compile(
    re.escape(HANDOFF_OPEN) + r'(.*?)' + re.escape(HANDOFF_CLOSE), re.DOTALL,
)

# The summary is the newest assistant text; a window this wide still finds it
# when the agent follows it with a short closing line or two.
_ASSISTANT_TEXTS_SCANNED = 20


def handoff_summary_from_texts(assistant_texts) -> str:
    """The newest non-blank handoff block in ``assistant_texts`` (oldest first).

    ``''`` when there is none.
    """
    for text in reversed(list(assistant_texts or [])):
        blocks = [
            block.strip() for block in _HANDOFF_BLOCK.findall(str(text or ''))
        ]
        blocks = [block for block in blocks if block]
        if blocks:
            return blocks[-1]
    return ''


def handoff_summary_from_events(events) -> str:
    """The handoff summary in a chat's events, or ``''``.

    Takes a live session's ``recent_events()`` (objects with ``event_type`` and
    ``raw``) or the transcript records read from disk (plain dicts carrying
    ``type``). Only ASSISTANT text is searched: the prompt that asks for the
    summary quotes the markers too.
    """
    shaped = [
        event if hasattr(event, 'event_type') else SimpleNamespace(
            event_type=str(event.get('type', '') or ''), raw=event,
        )
        for event in (events or [])
        if hasattr(event, 'event_type') or isinstance(event, dict)
    ]
    inputs = build_inputs_from_session(
        task_id='',
        task_summary='',
        branch_name='',
        workspace_path='',
        repository_paths=[],
        recent_events=shaped,
        max_recent_assistant=_ASSISTANT_TEXTS_SCANNED,
    )
    return handoff_summary_from_texts(inputs.recent_assistant_texts)


def new_chat_opening_message(summary: str) -> str:
    """The first message of the new chat: what it continues, then the summary."""
    return (
        'This chat continues the previous chat on this task, which ran low on '
        'context. Below is the handoff summary written at the end of it. Treat '
        'it as what you already know, confirm the current state of the files '
        'in the workspace before you change anything, and then carry on from '
        'its next steps.\n\n'
        f'{str(summary or "").strip()}'
    )
