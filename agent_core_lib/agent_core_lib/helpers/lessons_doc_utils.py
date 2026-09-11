from __future__ import annotations

import logging
import re
from pathlib import Path

from agent_core_lib.agent_core_lib.helpers.cached_file_render import (
    cached_file_render,
)

_TIMESTAMP_PATTERN = re.compile(r'^<!-- last_compacted:.*-->$')

# BY PATH, NOT INLINED.
#
# This used to paste the whole lessons file into the appended system prompt,
# capped at 50,000 characters. On Windows that alone pushed the spawn command
# line to ~37.6K against a hard 32,767 limit, and the CLI never started:
#
#     send failed: failed to launch claude CLI binary "claude":
#     [WinError 206] The filename or extension is too long
#
# A directive naming the file is a few hundred characters whatever the file
# grows to, so the prompt size stops tracking the lessons file at all — and
# the truncation that silently dropped everything past 50K goes with it.
#
# Reading it is NOT left to the prompt's good intentions: the caller pairs
# this with a PreToolUse hook that refuses every other tool until the file has
# been read in this session. Same shape as ``read_architecture_doc``.
_LESSONS_DIRECTIVE_TEMPLATE = (
    'Codebase-specific lessons learned across previous tasks: {path}\n'
    'BEFORE YOUR FIRST OTHER TOOL CALL, use the Read tool to read this '
    'file. Every other tool is blocked until you do.\n'
    '\n'
    'It holds concrete rules extracted from real mistakes made on prior '
    'tasks in this codebase. Treat them as additional constraints on your '
    'work — alongside the task description, not in conflict with it. If a '
    'lesson seems irrelevant to the current task, ignore it; do not invent '
    'work to satisfy a rule that does not apply.\n'
)


def read_lessons_file(
    path: str,
    *,
    logger: logging.Logger | None = None,
) -> str:
    def render(file_path: Path) -> str:
        # Still READ here, purely to decide whether there is anything worth
        # pointing at: a missing or blank lessons file must inject nothing,
        # or every fresh workspace would order the agent to read an empty
        # file (and the hook would block it on one).
        try:
            raw = file_path.read_text(encoding='utf-8')
        except OSError as exc:
            if logger is not None:
                logger.warning('failed to read lessons file at %s: %s', file_path, exc)
            return ''
        if not _strip_timestamp_header(raw).strip():
            return ''
        return _LESSONS_DIRECTIVE_TEMPLATE.format(path=str(file_path))

    return cached_file_render(path, render, logger=logger)


def _strip_timestamp_header(text: str) -> str:
    if not text:
        return ''
    lines = text.splitlines()
    if lines and _TIMESTAMP_PATTERN.match(lines[0]):
        return '\n'.join(lines[1:]).lstrip('\n')
    return text
