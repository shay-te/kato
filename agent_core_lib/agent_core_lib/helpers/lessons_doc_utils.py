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
# A directive naming the file is a couple of thousand characters whatever the
# file grows to, so the prompt size stops tracking the lessons file at all —
# and the truncation that silently dropped everything past 50K goes with it.
#
# Reading it is NOT left to the prompt's good intentions: the caller pairs
# this with a PreToolUse hook that refuses every other tool until the file has
# been read in this session.
#
# ONE DOCUMENT, READ AND WRITTEN BY THE AGENT.
#
# There used to be two: this file, written only by a background extractor, and
# a separate "architecture document" the agent maintained by hand. They held
# the same kind of thing — conventions, hidden contracts, rules learned from
# mistakes — in two places with two sets of rules, so the same rule ended up
# in both, neither was ever pruned, and every task opened by reading the pair.
# The agent that just did the work is the best editor of what the work taught,
# so it keeps the one file.

#: The file is re-read IN FULL at the start of every task, so its size is paid
#: on every one of them. Around this many characters is what a single Read
#: returns; past it the agent has to page through the file before it can start,
#: and a rule on page three is a rule that gets skimmed. Over budget, the
#: directive stops asking for a net-neutral edit and requires a smaller file.
LESSONS_BUDGET_CHARS = 80_000

_LESSONS_DIRECTIVE_TEMPLATE = (
    'Lessons — what earlier tasks in this workspace learned: {path}\n'
    'BEFORE YOUR FIRST OTHER TOOL CALL, use the Read tool to read this '
    'file — all of it. Every other tool is blocked until every line has '
    'been read.{size_note}\n'
    '\n'
    'It is the one document that carries knowledge from task to task: the '
    'map of the workspace, its conventions and hidden contracts, and '
    'concrete rules extracted from real mistakes — including corrections '
    'the operator had to make by hand. Treat them as constraints on your '
    'work, alongside the task description, not in conflict with it. If a '
    'lesson seems irrelevant to the current task, ignore it; do not invent '
    'work to satisfy a rule that does not apply.\n'
    '\n'
    'You also keep it accurate AND small, because the next task starts '
    'by reading every line of it. When this task taught something the '
    'file did not already say — the operator corrected you, a hidden '
    'contract cost you time, a convention was not written down — record '
    'the durable rule. Record only what the code cannot tell the next '
    'reader: a convention, a contract, a gotcha, a layer boundary, a "why '
    'we do it this way". It is a navigation aid, not a mirror of the '
    'source.\n'
    '\n'
    'Before writing, search the WHOLE file for what you are about to '
    'say, not just the section you are working in — the same rule in two '
    'sections is how this document rots. Already there: sharpen it in '
    'place or leave it. Wrong or superseded: REPLACE it, never leave a '
    'second version beside the first. Put a rule where its scope is: one '
    'that holds workspace-wide goes in the shared-conventions section, '
    'once, not in the area where you hit it.\n'
    '\n'
    '{edit_rule} Write the durable rule, not the incident that taught it '
    '— no dated narratives, no ticket history, no superseded numbers.\n'
    '\n'
    'Edit at most once per task, near the end, and re-read it immediately '
    'before editing — others edit it too. Just edit the file and stop — '
    'you must NEVER run git. Whether the edit is committed is the '
    'operator\'s call, not yours.\n'
)

_EDIT_RULE_WITHIN_BUDGET = (
    'Prefer a net-neutral or net-shorter edit: when you add, delete what '
    'it makes redundant.'
)
_EDIT_RULE_OVER_BUDGET = (
    'The file is OVER its size budget ({size_kb} KB against {budget_kb} '
    'KB), so your edit this task must leave it SMALLER than you found it: '
    'merge rules that say the same thing, cut incident narrative, delete '
    'what the code already makes obvious.'
)
_SIZE_NOTE_PAGED = (
    ' It is {size_kb} KB, more than one Read returns: read ALL of it, in '
    'consecutive chunks (offset / limit), before you start.'
)


def _directive_for(file_path: Path, body: str) -> str:
    size = len(body)
    size_kb = round(size / 1000)
    over_budget = size > LESSONS_BUDGET_CHARS
    return _LESSONS_DIRECTIVE_TEMPLATE.format(
        path=str(file_path),
        size_note=_SIZE_NOTE_PAGED.format(size_kb=size_kb) if over_budget else '',
        edit_rule=(
            _EDIT_RULE_OVER_BUDGET.format(
                size_kb=size_kb, budget_kb=round(LESSONS_BUDGET_CHARS / 1000),
            ) if over_budget else _EDIT_RULE_WITHIN_BUDGET
        ),
    )


def read_lessons_file(
    path: str,
    *,
    logger: logging.Logger | None = None,
) -> str:
    def render(file_path: Path) -> str:
        # Still READ here: to decide whether there is anything worth pointing
        # at — a missing or blank lessons file must inject nothing, or every
        # fresh workspace would order the agent to read an empty file (and the
        # hook would block it on one) — and to tell the agent how much there
        # is, which changes what it is asked to do about it.
        try:
            raw = file_path.read_text(encoding='utf-8')
        except OSError as exc:
            if logger is not None:
                logger.warning('failed to read lessons file at %s: %s', file_path, exc)
            return ''
        body = _strip_timestamp_header(raw)
        if not body.strip():
            return ''
        return _directive_for(file_path, body)

    return cached_file_render(path, render)


def _strip_timestamp_header(text: str) -> str:
    if not text:
        return ''
    lines = text.splitlines()
    if lines and _TIMESTAMP_PATTERN.match(lines[0]):
        return '\n'.join(lines[1:]).lstrip('\n')
    return text
