"""Per-workspace composer-draft store at ``<workspace>/.kato-prompts.json``.

Persists the operator's in-progress composer prompt (the typed text AND any
pasted/dropped image attachments) on the SERVER so it survives a browser
refresh, a different browser, and switching between tasks — unlike browser
localStorage / IndexedDB, which is per-browser and gets wiped by private mode
or cleared site data.

One draft per task. The file sits alongside ``.kato-comments.json`` in the
workspace and is DELIBERATELY separate from ``.kato-meta.json``: the metadata is
the critical workspace record (read on every list/status/publish), whereas a
draft is high-churn (written on every keystroke) and can carry large base64
image data — co-locating them would risk corrupting the record and bloating it.

Best-effort throughout: a read/write failure degrades to an empty draft rather
than raising, so the composer keeps working.
"""
from __future__ import annotations

import json
from pathlib import Path

from kato_core_lib.helpers.atomic_json_utils import atomic_write_json
from kato_core_lib.helpers.logging_utils import configure_logger

DRAFT_FILENAME = '.kato-prompts.json'
_logger = configure_logger('PromptDraftStore')


def draft_path(workspace_dir) -> Path:
    return Path(workspace_dir) / DRAFT_FILENAME


def _clean_images(images) -> list[dict]:
    """Keep only well-formed Anthropic image parts ({media_type, data})."""
    if not isinstance(images, list):
        return []
    cleaned: list[dict] = []
    for image in images:
        if isinstance(image, dict) and image.get('media_type') and image.get('data'):
            cleaned.append({
                'media_type': str(image['media_type']),
                'data': str(image['data']),
            })
    return cleaned


def read_draft(workspace_dir) -> dict:
    """Return ``{'text': str, 'images': list}`` for the task (empty when none)."""
    path = draft_path(workspace_dir)
    try:
        if not path.is_file():
            return {'text': '', 'images': []}
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning('unreadable composer draft at %s: %s', path, exc)
        return {'text': '', 'images': []}
    if not isinstance(payload, dict):
        return {'text': '', 'images': []}
    return {
        'text': str(payload.get('text') or ''),
        'images': _clean_images(payload.get('images')),
    }


def write_draft(workspace_dir, text, images) -> None:
    """Persist the draft. An empty text with no images deletes the file."""
    text = str(text or '')
    images = _clean_images(images)
    if not text.strip() and not images:
        clear_draft(workspace_dir)
        return
    # Only persist when the workspace already exists on disk. Don't create a
    # dir just for a draft — a workspace folder with no .kato-meta.json surfaces
    # as an ERRORED workspace in the UI. Until the task is picked up, the
    # browser cache holds the draft.
    if not Path(workspace_dir).is_dir():
        return
    atomic_write_json(
        draft_path(workspace_dir), {'text': text, 'images': images},
        logger=_logger, label='composer draft',
    )


def clear_draft(workspace_dir) -> None:
    """Remove the draft file (no-op if absent). Best-effort."""
    path = draft_path(workspace_dir)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        _logger.warning('failed to clear composer draft at %s: %s', path, exc)
