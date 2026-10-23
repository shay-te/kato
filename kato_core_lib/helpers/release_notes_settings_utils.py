"""The release-notes settings: on/off, where the file is, where else it goes.

Read FRESH on every use — ``~/.kato/settings.json`` first (what the Settings
drawer writes), then the shell environment, then the default — so a change in
Settings applies to the next finished task without restarting kato. The same
order the review-comment switch uses (``review_comment_gate_utils``).

The file defaults to ``~/.kato/release-notes.md``. A path inside the
workspaces root is refused: there it would sit in a task's clone, and the
publish step stages everything in a clone — kato would end up pushing it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from kato_core_lib.helpers.kato_paths_utils import configured_path
from kato_core_lib.helpers.kato_settings_store_utils import read_kato_settings

RELEASE_NOTES_ENABLED_KEY = 'KATO_RELEASE_NOTES_ENABLED'
RELEASE_NOTES_PATH_KEY = 'KATO_RELEASE_NOTES_PATH'
RELEASE_NOTES_DESTINATION_KEY = 'KATO_RELEASE_NOTES_DESTINATION'
RELEASE_NOTES_GOOGLE_DOC_URL_KEY = 'KATO_RELEASE_NOTES_GOOGLE_DOC_URL'
RELEASE_NOTES_GOOGLE_CREDENTIALS_KEY = 'KATO_RELEASE_NOTES_GOOGLE_CREDENTIALS_PATH'

RELEASE_NOTES_KEYS = frozenset({
    RELEASE_NOTES_ENABLED_KEY,
    RELEASE_NOTES_PATH_KEY,
    RELEASE_NOTES_DESTINATION_KEY,
    RELEASE_NOTES_GOOGLE_DOC_URL_KEY,
    RELEASE_NOTES_GOOGLE_CREDENTIALS_KEY,
})

DESTINATION_FILE = 'file'
DESTINATION_FILE_AND_GOOGLE_DOC = 'file_and_google_doc'
DESTINATIONS = (DESTINATION_FILE, DESTINATION_FILE_AND_GOOGLE_DOC)

_DEFAULT_FILENAME = 'release-notes.md'
_FALSY = ('false', '0', 'no', 'off')
_GOOGLE_DOC_ID = re.compile(r'/document/(?:u/\d+/)?d/([A-Za-z0-9_-]{20,})')
_BARE_ID = re.compile(r'^[A-Za-z0-9_-]{20,}$')


def _setting(key: str, env: dict | None) -> str:
    """settings.json (live) → shell env → '' (unset)."""
    try:
        settings = read_kato_settings()
    except Exception:  # noqa: BLE001 - a corrupt settings file must not decide it
        settings = {}
    for source in (settings, os.environ if env is None else env):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def release_notes_enabled(env: dict | None = None) -> bool:
    """On unless the operator switched it off."""
    return _setting(RELEASE_NOTES_ENABLED_KEY, env).lower() not in _FALSY


def release_notes_path(env: dict | None = None) -> Path:
    """The configured file, ``~`` expanded; ``~/.kato/release-notes.md`` when unset."""
    configured = configured_path(_setting(RELEASE_NOTES_PATH_KEY, env))
    return configured if configured is not None else Path.home() / '.kato' / _DEFAULT_FILENAME


def release_notes_destination(env: dict | None = None) -> str:
    """Where entries go besides the file. Anything unknown means the file only."""
    value = _setting(RELEASE_NOTES_DESTINATION_KEY, env).lower()
    return value if value in DESTINATIONS else DESTINATION_FILE


def google_doc_id(env: dict | None = None) -> str:
    """The Google Doc's id from its link (or a bare id); '' when there is none."""
    return google_doc_id_from(_setting(RELEASE_NOTES_GOOGLE_DOC_URL_KEY, env))


def google_doc_id_from(value: str) -> str:
    text = str(value or '').strip()
    match = _GOOGLE_DOC_ID.search(text)
    if match:
        return match.group(1)
    return text if _BARE_ID.match(text) else ''


def google_credentials_path(env: dict | None = None) -> Path | None:
    return configured_path(_setting(RELEASE_NOTES_GOOGLE_CREDENTIALS_KEY, env))


def workspaces_root(env: dict | None = None) -> Path:
    """Where task clones live — settings.json, then env, then ``~/.kato/workspaces``."""
    configured = configured_path(_setting('KATO_WORKSPACES_ROOT', env))
    return configured if configured is not None else Path.home() / '.kato' / 'workspaces'


def path_problem(path: Path | str, *, workspaces: Path | str) -> str:
    """Why ``path`` cannot hold the release notes ('' when it can)."""
    candidate = Path(path).expanduser()
    root = Path(workspaces).expanduser()
    try:
        inside = candidate.resolve().is_relative_to(root.resolve())
    except OSError:
        inside = False
    if inside:
        return (
            f'{candidate} is inside the workspaces folder ({root}); a file there sits in '
            f'a task\'s clone and would be committed with it. Choose a path outside it.'
        )
    if candidate.exists() and candidate.is_dir():
        return f'{candidate} is a folder; give the path of a file, e.g. {candidate / _DEFAULT_FILENAME}'
    return ''


def settings_errors(updates: dict[str, str]) -> list[str]:
    """Problems with release-notes values about to be saved from Settings."""
    errors: list[str] = []
    path_value = str(updates.get(RELEASE_NOTES_PATH_KEY, '') or '').strip()
    if path_value:
        root_value = str(updates.get('KATO_WORKSPACES_ROOT', '') or '').strip()
        root = configured_path(root_value) or workspaces_root()
        problem = path_problem(configured_path(path_value) or Path(path_value), workspaces=root)
        if problem:
            errors.append(f'{RELEASE_NOTES_PATH_KEY}: {problem}')
    doc_value = str(updates.get(RELEASE_NOTES_GOOGLE_DOC_URL_KEY, '') or '').strip()
    if doc_value and not google_doc_id_from(doc_value):
        errors.append(
            f'{RELEASE_NOTES_GOOGLE_DOC_URL_KEY}: paste the Google Doc\'s link '
            f'(https://docs.google.com/document/d/…); got "{doc_value}"',
        )
    return errors
