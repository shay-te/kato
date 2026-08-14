"""The ticket's own text, rendered as a prompt block.

Three prompt builders need the same thing — "here is the task you are working
on" — and each used to spell it out differently:

* the autonomous implementation run (``ClaudeCliClient._build_implementation_prompt``)
  wrapped it as untrusted content,
* the ``kato:wait-planning`` chat prompt inlined it as raw markdown, and
* a plain chat tab never sent it at all, so the operator's first message
  landed on an agent that had no idea what the ticket said.

One builder, used by all of them. The body is ALWAYS framed with
``wrap_untrusted_workspace_content``: ``summary`` and ``description`` come from
the issue tracker and may be written by anyone with comment access there, so
the model has to be able to tell them apart from kato's own scaffolding. Only
``task_id`` is orchestrator-controlled and left unwrapped.
"""

from __future__ import annotations

from sandbox_core_lib.sandbox_core_lib.workspace_delimiter import (
    wrap_untrusted_workspace_content,
)
from utils_core_lib.utils_core_lib.text_utils import normalized_text


def task_definition_block(
    *,
    task_id: object = '',
    summary: object = '',
    description: object = '',
) -> str:
    """The ticket's summary + description as one untrusted-framed block.

    Returns ``''`` when there is no text to show — callers treat an empty
    block as "nothing to prepend" rather than emitting an empty heading.
    """
    normalized_summary = normalized_text(summary)
    normalized_description = normalized_text(description)
    if not normalized_summary and not normalized_description:
        return ''
    normalized_id = normalized_text(task_id)
    body = '\n\n'.join(
        part for part in (normalized_summary, normalized_description) if part
    )
    framed = wrap_untrusted_workspace_content(
        body,
        source_path=f'task:{normalized_id}' if normalized_id else 'task',
    )
    heading = (
        f'## Task definition — {normalized_id}'
        if normalized_id
        else '## Task definition'
    )
    return f'{heading}\n{framed}'
