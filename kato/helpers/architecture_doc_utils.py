"""Read an optional architecture/project-context doc for Claude prompts.

When ``KATO_ARCHITECTURE_DOC_PATH`` is set, kato appends the file's
contents to Claude's system prompt on every spawn. This is fed via
``claude -p --append-system-prompt <text>`` so it lands in the cached
system-prompt slot and applies equally to fresh sessions and resumed
ones (Claude rebuilds the system prompt on every process spawn).

The doc is re-read on every spawn so editing it takes effect on the
next turn without restarting kato. Read errors are logged and treated
as "no doc configured" — the orchestrator never blocks on this.
"""

from __future__ import annotations

import logging
from pathlib import Path

from kato.helpers.text_utils import normalized_text


_MAX_CHARS = 200_000


def read_architecture_doc(
    path: str,
    *,
    logger: logging.Logger | None = None,
) -> str:
    """Return the architecture-doc text, or '' when nothing is configured.

    The result is trimmed and capped at ``_MAX_CHARS`` so a runaway file
    can't blow the system-prompt budget. ``path`` may be an empty string
    (returns ''), a missing file (warns once + returns ''), or a real
    file (returns content).
    """
    normalized = normalized_text(path)
    if not normalized:
        return ''
    file_path = Path(normalized).expanduser()
    if not file_path.is_file():
        if logger is not None:
            logger.warning(
                'architecture doc path %s is not a file; skipping context injection',
                file_path,
            )
        return ''
    try:
        text = file_path.read_text(encoding='utf-8')
    except OSError as exc:
        if logger is not None:
            logger.warning(
                'failed to read architecture doc at %s: %s', file_path, exc,
            )
        return ''
    text = text.strip()
    if not text:
        return ''
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS]
    return text
