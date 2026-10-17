"""Read the proposer's split out of its reply — strictly.

A split decides what work gets created, so a reply without a readable
``<task-split>`` block, or with a step that breaks a rule, is an error with a
reason, never a guess. The LAST block in the reply is the answer: a reply that
quotes the format earlier must not be read as its proposal.

With ``files`` (a split the operator already wrote, path → text) each step
names its ``file`` instead of carrying a brief: the brief is that file's text,
exactly, and every file must be used once — a skipped or repeated file is an
error, never a silent drop.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from plan_queue_core_lib.plan_queue_core_lib.graph import (
    SplitValidationError,
    StepDraft,
    validate_split,
)
from plan_queue_core_lib.plan_queue_core_lib.proposer_prompt import SPLIT_CLOSE, SPLIT_OPEN


class SplitProposalError(ValueError):
    """The reply did not carry a usable split; the message says why."""


@dataclass(frozen=True)
class SplitProposal(object):
    steps: tuple[StepDraft, ...]
    notes: str = ''
    files: tuple[str, ...] = ()  # per step, the file it was made from (files mode)


def parse_task_split(
    text: str,
    *,
    allowed_repositories: Iterable[str],
    files: Mapping[str, str] | None = None,
) -> SplitProposal:
    body = _strip_fence(_last_block(text or ''))
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise SplitProposalError(f'the {SPLIT_OPEN} block is not valid JSON: {exc}') from exc
    if not isinstance(payload, dict) or not isinstance(payload.get('steps'), list):
        raise SplitProposalError(f'the {SPLIT_OPEN} block has no "steps" list')
    items = list(enumerate(payload['steps'], start=1))
    sources: tuple[str, ...] = ()
    if files is None:
        drafts = [_draft(item, index) for index, item in items]
    else:
        drafts = [_draft(item, index, brief=_file_text(item, index, files)) for index, item in items]
        sources = _require_every_file(payload['steps'], files)
    try:
        steps = validate_split(drafts, allowed_repositories=allowed_repositories)
    except SplitValidationError as exc:
        raise SplitProposalError(str(exc)) from exc
    notes = payload.get('notes', '')
    return SplitProposal(
        steps=tuple(steps), notes=notes.strip() if isinstance(notes, str) else '', files=sources,
    )


def _last_block(text: str) -> str:
    end = text.rfind(SPLIT_CLOSE)
    start = text.rfind(SPLIT_OPEN, 0, end if end >= 0 else len(text))
    if start < 0 or end < 0:
        raise SplitProposalError(f'the reply has no {SPLIT_OPEN} block')
    return text[start + len(SPLIT_OPEN):end].strip()


def _strip_fence(body: str) -> str:
    """Agents often wrap the JSON in a ```json fence inside the block."""
    if not body.startswith('```'):
        return body
    lines = body.splitlines()[1:]
    if lines and lines[-1].strip() == '```':
        lines = lines[:-1]
    return '\n'.join(lines).strip()


def _draft(item, index: int, *, brief: str | None = None) -> StepDraft:
    if not isinstance(item, dict):
        raise SplitProposalError(f'step {index} is not an object')
    required = ('id', 'title') if brief is not None else ('id', 'title', 'brief')
    for key in required:
        if not isinstance(item.get(key), str):
            raise SplitProposalError(f'step {index} has no text "{key}"')
    return StepDraft(
        id=item['id'].strip(),
        title=item['title'],
        brief=item['brief'] if brief is None else brief,
        depends_on=_string_list(item.get('depends_on'), 'depends_on', index),
        repositories=_string_list(item.get('repositories'), 'repositories', index),
    )


def _string_list(value, key: str, index: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(entry, str) for entry in value):
        raise SplitProposalError(f'step {index} has a "{key}" that is not a list of text')
    return tuple(entry.strip() for entry in value if entry.strip())


def _file_text(item, index: int, files: Mapping[str, str]) -> str:
    """The brief of a step made from a file: the file's text, exactly."""
    if not isinstance(item, dict):
        raise SplitProposalError(f'step {index} is not an object')
    path = item.get('file')
    if not isinstance(path, str) or not path.strip():
        raise SplitProposalError(f'step {index} names no "file"')
    if path.strip() not in files:
        raise SplitProposalError(f'step {index} names "{path.strip()}", which is not one of the files')
    return files[path.strip()]


def _require_every_file(items: list, files: Mapping[str, str]) -> tuple[str, ...]:
    """Each step's file, in step order — once every file is used exactly once."""
    used = [item['file'].strip() for item in items]
    repeated = sorted({path for path in used if used.count(path) > 1})
    if repeated:
        raise SplitProposalError('more than one step was made from ' + ', '.join(repeated))
    missing = [path for path in files if path not in used]
    if missing:
        raise SplitProposalError('no step was made from ' + ', '.join(missing))
    return tuple(used)
