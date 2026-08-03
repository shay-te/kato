"""Backend-owned store for remembered tool-permission decisions.

"Allow always" / "Deny always" used to be a browser-only concept —
the operator's choice lived in ``localStorage`` and the frontend
independently decided whether to auto-resolve a NEW pending permission
ask without ever showing it, then submitted that decision to the
backend like any other click. The backend re-validates every decision
it receives (Action Guard, sandbox scope) regardless of who submitted
it, so unsafe actions were never actually at risk — but the CHOICE of
"does this need to ask a human" was the client's alone, with zero
server-side memory of it.

This store moves that choice server-side: the webserver checks it
BEFORE a pending request is ever surfaced to the browser (see
``_maybe_auto_resolve_pending`` in ``kato_webserver/app.py``), so the
client is never the one deciding what gets approved — it only ever
sees requests the backend has already determined need a human.

Stored as a flat ``{key: "allow"|"deny"}`` map at
``~/.kato/tool_decisions.json`` (override via
``KATO_TOOL_DECISIONS_PATH``). Each key joins the tool name and the
command signature with a single space (tool names never contain a
space, so splitting back on the first space is unambiguous even though
a multi-program command signature like "docker mvn" does). The
signature must be computed identically on both sides — see
``tool_decision_utils.py``, a line-for-line port of
``permissionEnvelope.js`` — so a decision remembered here always
matches the same request the browser would have shown.
"""
from __future__ import annotations

import json
import threading

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json
from kato_core_lib.helpers.kato_paths_utils import kato_home_path

_ENV_KEY = 'KATO_TOOL_DECISIONS_PATH'
_FILENAME = 'tool_decisions.json'
_KEY_SEPARATOR = ' '

_lock = threading.Lock()


def _path():
    return kato_home_path(_FILENAME, env_key=_ENV_KEY)


def _key(tool_name: str, command_signature: str) -> str:
    return str(tool_name or '') + _KEY_SEPARATOR + str(command_signature or '')


def _split_key(key: str) -> tuple[str, str]:
    tool_name, _, command_signature = str(key or '').partition(_KEY_SEPARATOR)
    return tool_name, command_signature


def read_tool_decisions() -> dict[str, str]:
    """Raw ``{key: "allow"|"deny"}`` map (empty on missing/unreadable file)."""
    path = _path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(k): str(v)
        for k, v in data.items()
        if str(v) in ('allow', 'deny')
    }


def list_tool_decisions() -> list[dict[str, object]]:
    """``[{tool_name, command_signature, allow}, ...]`` for the Settings
    -> Permissions panel -- reads straight from the backend store, not
    the browser's (removed) localStorage cache."""
    entries = []
    for key, decision in read_tool_decisions().items():
        tool_name, command_signature = _split_key(key)
        entries.append({
            'tool_name': tool_name,
            'command_signature': command_signature,
            'allow': decision == 'allow',
        })
    entries.sort(key=lambda entry: (entry['tool_name'], entry['command_signature']))
    return entries


def recall_tool_decision(tool_name: str, command_signature: str) -> bool | None:
    """``True``/``False`` for a remembered allow/deny, or ``None`` if
    nothing is remembered for this (tool, signature) pair."""
    decision = read_tool_decisions().get(_key(tool_name, command_signature))
    if decision is None:
        return None
    return decision == 'allow'


def remember_tool_decision(tool_name: str, command_signature: str, allow: bool) -> None:
    """Persist an "Allow always" / "Deny always" choice. Best-effort --
    a write failure is swallowed (mirrors ``plan_mode_store``'s
    best-effort persistence): losing one remembered-decision write just
    means the operator is asked again next time, never a crash."""
    tool_name = str(tool_name or '')
    command_signature = str(command_signature or '')
    if not tool_name:
        return
    with _lock:
        decisions = read_tool_decisions()
        decisions[_key(tool_name, command_signature)] = 'allow' if allow else 'deny'
        _write(decisions)


def forget_tool_decision(tool_name: str, command_signature: str) -> None:
    """Remove a remembered decision, if any. Idempotent."""
    with _lock:
        decisions = read_tool_decisions()
        key = _key(tool_name, command_signature)
        if key not in decisions:
            return
        decisions.pop(key, None)
        _write(decisions)


def clear_all_tool_decisions() -> None:
    """Drop every remembered decision. Idempotent — the Settings panel's
    "Clear all" action."""
    with _lock:
        if not read_tool_decisions():
            return
        _write({})


def _write(decisions: dict[str, str]) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    atomic_write_json(path, decisions)
