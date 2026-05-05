"""Read the global compacted lessons file for Claude prompts.

Mirrors ``architecture_doc_utils`` but for the lessons subsystem:
the global ``lessons.md`` file (compacted by ``LessonsService``) is
re-read on every Claude spawn and its body is appended to the system
prompt. Editing the file (or running a compact) takes effect on the
next turn without restarting kato.

The compaction-timestamp header line is stripped before injection —
Claude doesn't need to read its own bookkeeping. Read errors are
logged and treated as "no lessons" so a missing or unreadable file
never blocks the spawn.
"""

from __future__ import annotations

import logging
from pathlib import Path

from kato_core_lib.data_layers.data_access.lessons_data_access import (
    strip_timestamp_header,
)
from kato_core_lib.helpers.file_cache_utils import stat_keyed_cache


_MAX_BODY_CHARS = 50_000


_LESSONS_DIRECTIVE_TEMPLATE = (
    'Codebase-specific lessons learned by Kato across previous tasks '
    '(location: {path}). These are concrete rules extracted from real '
    'mistakes that happened on prior tasks in this codebase. Treat '
    'them as additional constraints on your work — alongside the '
    'task description, not in conflict with it. If a lesson seems '
    'irrelevant to the current task, ignore it; do not invent work to '
    'satisfy a rule that does not apply.\n'
    '\n'
    '--- BEGIN LEARNED LESSONS ---\n'
    '{text}\n'
    '--- END LEARNED LESSONS ---\n'
)

# Process-local cache. Lessons files can be tens of kB after
# compaction, so the avoided IO is real. Shared cache helper keeps
# invalidation aligned with the architecture-doc reader; see
# ``file_cache_utils`` for the strategy.
_load_cached, _reset_cache = stat_keyed_cache()

# Module-level slot for the logger used by the active call. Threaded
# through ``_build_directive`` without changing the public function
# signature (``stat_keyed_cache`` calls ``compute(file_path)`` with
# the path only). Set + restored under the assumption that operator
# CLIs are single-threaded; the spawn-time ``RuntimeError`` we saw
# was specifically about per-thread cache contention, not logging.
_active_logger: logging.Logger | None = None


def read_lessons_file(
    path: str,
    *,
    logger: logging.Logger | None = None,
) -> str:
    """Return the wrapped lessons body, or '' when absent / empty.

    ``path`` is the global ``lessons.md`` file. May be empty (returns
    ''), missing (silent return — lessons are optional), unreadable
    (warns + returns ''), or populated (returns wrapped body).
    """
    global _active_logger
    previous_logger = _active_logger
    _active_logger = logger
    try:
        result = _load_cached(path, _build_directive)
    finally:
        _active_logger = previous_logger
    return result if result is not None else ''


def _build_directive(file_path: Path) -> str:
    try:
        raw = file_path.read_text(encoding='utf-8')
    except OSError as exc:
        if _active_logger is not None:
            _active_logger.warning(
                'failed to read lessons file at %s: %s', file_path, exc,
            )
        return ''
    body = strip_timestamp_header(raw).strip()
    if not body:
        return ''
    if len(body) > _MAX_BODY_CHARS:
        body = body[:_MAX_BODY_CHARS]
    return _LESSONS_DIRECTIVE_TEMPLATE.format(
        path=str(file_path),
        text=body,
    )
