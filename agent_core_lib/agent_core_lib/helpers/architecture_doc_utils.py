from __future__ import annotations

import logging

from agent_core_lib.agent_core_lib.helpers.cached_file_render import (
    cached_file_render,
)

_LIVING_DOC_DIRECTIVE_TEMPLATE = (
    'Project architecture document: {path}\n'
    'At the start of every task, use the Read tool to read this '
    'file. It contains the canonical map of the workspace and any '
    'non-obvious conventions, hidden contracts, gotchas, and layer '
    'boundaries the project has accumulated. Let it shape your '
    'plan.\n'
    '\n'
    'You keep it accurate AND small. Every line is re-read at the start '
    'of every future task, so an edit that does not earn its size slows '
    'every later one. Record only what the code cannot tell the next '
    'reader — a convention, a hidden contract, a gotcha, a layer '
    'boundary, a "why we do it this way". It is a navigation aid, not a '
    'mirror of the source.\n'
    '\n'
    'Before writing, search the WHOLE file for what you are about to '
    'say, not just the section you are working in — the same rule in '
    'two sections is how this document rots. Already there: sharpen it '
    'in place or leave it. Wrong or superseded: REPLACE it, never leave '
    'a second version beside the first.\n'
    '\n'
    'Put a rule where its scope is: one that holds workspace-wide goes '
    'in the shared-conventions section, once, not in the area where you '
    'hit it. Add a section only when nothing covers the subject.\n'
    '\n'
    'Prefer a net-neutral or net-shorter edit: when you add, delete '
    'what it makes redundant. Write the durable rule, not the incident '
    'that taught it — no dated narratives, no ticket history, no '
    'superseded numbers. If the file is too big to afford, say so '
    'instead of adding to it.\n'
    '\n'
    'Edit at most once per task, near the end, and re-read it '
    'immediately before editing — others edit it too. Just edit the '
    'file and stop — you must NEVER run git. Whether the edit is '
    'committed is the operator\'s call, not yours.\n'
)


def read_architecture_doc(
    path: str,
    *,
    logger: logging.Logger | None = None,
) -> str:
    return cached_file_render(
        path,
        lambda file_path: _LIVING_DOC_DIRECTIVE_TEMPLATE.format(path=str(file_path)),
        logger=logger,
        stat_error_message=(
            'architecture doc path %s is not a file; skipping context injection'
        ),
    )
