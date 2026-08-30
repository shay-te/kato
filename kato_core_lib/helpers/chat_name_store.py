"""Operator-given names for chat sessions.

A chat had no name of its own. The list in the chats menu labelled each one
with its first user message, which is a reasonable guess and a poor name: the
opening line of a conversation is usually the least memorable thing about it,
and two chats that began "fix the failing test" are indistinguishable a week
later.

So this is the one place a real name lives. It is a per-chat operator label
and nothing else reads it — the agent never sees it, and no behaviour keys on
it. An empty name deletes the entry, and the list falls back to the derived
label, so "rename" and "clear the name" are the same operation.

Keyed on ``agent_session_id`` rather than task id, deliberately. A task has
many chats over its life (start-new-chat detaches the old one and keeps it in
``previous_session_ids``), so a task-keyed name would follow whichever chat
happened to be active and re-label a conversation the operator never touched.
The id follows the conversation.

Stored at ``~/.kato/chat_names.json`` (override via ``KATO_CHAT_NAMES_PATH``).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json
from kato_core_lib.helpers.kato_paths_utils import kato_home_path

_ENV_KEY = 'KATO_CHAT_NAMES_PATH'
_FILENAME = 'chat_names.json'

# Longer than any list row can show, short enough that the file stays a name
# store rather than somewhere notes accumulate.
MAX_NAME_LENGTH = 120

# ``set_chat_name`` is read-modify-write over the whole file. Without this
# lock, two renames landing together can both read the old map before either
# writes, silently dropping one. Mirrors plan_mode_store / remote_control_store.
_lock = threading.Lock()


def _path() -> Path:
    return kato_home_path(_FILENAME, env_key=_ENV_KEY)


def _norm_id(chat_id: object) -> str:
    return str(chat_id or '').strip()


def _norm_name(name: object) -> str:
    """Collapse whitespace and cap the length.

    Newlines matter: the name is rendered in a single-line row, and a pasted
    multi-line string would either break the layout or be silently truncated
    at display time — better to store what will actually be shown.
    """
    collapsed = ' '.join(str(name or '').split())
    return collapsed[:MAX_NAME_LENGTH]


def read_chat_names() -> dict[str, str]:
    """Every stored name (empty when the file is missing or unreadable)."""
    path = _path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    names: dict[str, str] = {}
    for chat_id, name in data.items():
        key, value = _norm_id(chat_id), _norm_name(name)
        if key and value:
            names[key] = value
    return names


def chat_name(chat_id: str) -> str:
    """One chat's name, or '' when it has never been renamed."""
    key = _norm_id(chat_id)
    return read_chat_names().get(key, '') if key else ''


def set_chat_name(chat_id: str, name: object) -> str:
    """Store (or clear) a chat's name. Returns what was stored.

    A blank name removes the entry rather than storing an empty string, so the
    list falls back to its derived label and the file does not accumulate
    tombstones. Idempotent: writing the same name twice does not touch disk.
    """
    key = _norm_id(chat_id)
    if not key:
        return ''
    value = _norm_name(name)
    with _lock:
        names = read_chat_names()
        if names.get(key, '') == value:
            return value
        if value:
            names[key] = value
        else:
            names.pop(key, None)
        path = _path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        atomic_write_json(path, dict(sorted(names.items())))
    return value


def forget_chat_names(chat_ids) -> None:
    """Drop names for the given chats — used when a task is forgotten.

    Best-effort and idempotent; ids with no stored name are ignored.
    """
    wanted = {_norm_id(cid) for cid in (chat_ids or []) if _norm_id(cid)}
    if not wanted:
        return
    with _lock:
        names = read_chat_names()
        remaining = {k: v for k, v in names.items() if k not in wanted}
        if remaining == names:
            return
        atomic_write_json(_path(), dict(sorted(remaining.items())))
