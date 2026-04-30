"""Replay archived Claude CLI sessions as raw stream-json events.

Claude Code persists every conversation it runs as a JSONL file under
``~/.claude/projects/<encoded-cwd>/<session_id>.jsonl``. After a kato
restart the in-memory ``_recent_events`` buffer is empty, so the only
way to repopulate the chat is to read those JSONL files and feed them
back into the SSE backlog. This module is the read side of that
pipeline — pure I/O, no kato types — so it stays trivially testable.
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Iterable


_DEFAULT_PROJECTS_ROOT = Path.home() / '.claude' / 'projects'


def find_session_file(
    claude_session_id: str,
    *,
    projects_root: Path | str | None = None,
) -> Path | None:
    """Locate the JSONL transcript for ``claude_session_id``.

    Walks every ``~/.claude/projects/*/`` directory; Claude's per-project
    folder name is a lossy encoding of cwd (``/``, ``_`` and ``.`` all
    become ``-``), so reconstructing it deterministically is brittle —
    globbing is the simplest robust strategy.
    """
    session_id = (claude_session_id or '').strip()
    if not session_id:
        return None
    root = Path(projects_root) if projects_root else _DEFAULT_PROJECTS_ROOT
    if not root.is_dir():
        return None
    pattern = str(root / '*' / f'{session_id}.jsonl')
    matches = glob.glob(pattern)
    if not matches:
        return None
    return Path(matches[0])


def load_history_events(
    claude_session_id: str,
    *,
    projects_root: Path | str | None = None,
    max_events: int = 5000,
) -> list[dict]:
    """Read the JSONL transcript and return UI-friendly raw events.

    Filters out Claude-internal noise (queue ops, attachment metadata,
    summary records) so the chat shows just the conversation. Each
    returned dict has the same shape kato emits over the live stream:
    ``{'type': 'user'|'assistant'|'system'|..., 'message': {...}, ...}``.
    """
    path = find_session_file(claude_session_id, projects_root=projects_root)
    if path is None:
        return []
    events: list[dict] = []
    try:
        with path.open('r', encoding='utf-8') as fh:
            for raw_line in fh:
                event = _coerce_event(raw_line)
                if event is None:
                    continue
                events.append(event)
                if len(events) >= max_events:
                    break
    except OSError:
        return []
    return events


def _coerce_event(raw_line: str) -> dict | None:
    line = raw_line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    event_type = str(payload.get('type', '') or '')
    if event_type not in _RELEVANT_EVENT_TYPES:
        return None
    if event_type == 'user':
        message = payload.get('message')
        if _is_tool_result_only(message):
            return payload
        if not _has_displayable_text(message):
            return None
        if _is_kato_orchestration_prompt(message):
            return None
    return payload


_KATO_PROMPT_MARKERS = (
    'Security guardrails:',
    'Tool guardrails:',
    'Address pull request comment',
    'When you are done:',
)


def _is_kato_orchestration_prompt(message) -> bool:
    """True when the user message is kato's auto-injected task prompt.

    Those carry security/tool guardrails plus an explicit completion
    contract — useful to Claude, noise to a human reading the history.
    """
    if not isinstance(message, dict):
        return False
    content = message.get('content')
    blocks = content if isinstance(content, list) else []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        text = block.get('text') or ''
        if any(marker in text for marker in _KATO_PROMPT_MARKERS):
            return True
    return False


_RELEVANT_EVENT_TYPES = frozenset(
    {
        'user',
        'assistant',
        'system',
        'result',
    }
)


def _has_displayable_text(message) -> bool:
    if not isinstance(message, dict):
        return False
    content = message.get('content')
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get('type') == 'text' and (block.get('text') or '').strip():
            return True
    return False


def _is_tool_result_only(message) -> bool:
    if not isinstance(message, dict):
        return False
    content = message.get('content')
    if not isinstance(content, list) or not content:
        return False
    return all(
        isinstance(block, dict) and block.get('type') == 'tool_result'
        for block in content
    )


def iter_event_paths(
    *,
    projects_root: Path | str | None = None,
) -> Iterable[Path]:
    """Yield every JSONL transcript path on disk (debugging helper)."""
    root = Path(projects_root) if projects_root else _DEFAULT_PROJECTS_ROOT
    if not root.is_dir():
        return
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        for path in sorted(entry.glob('*.jsonl')):
            yield path
