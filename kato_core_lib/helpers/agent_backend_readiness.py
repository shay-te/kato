"""Can each chat backend actually take a message right now?

Both Claude and Codex ALWAYS get a tab in the chat pane. A tab whose CLI
is missing is not hidden — it opens a setup panel instead of a chat, which
is the only way an operator ever discovers the backend exists. That trade
needs a cheap, honest readiness answer per backend, which is what this is.

The probe is the transport's OWN ``validate_connection()``: it already
carries the actionable install/login text (``npm install -g @openai/codex``,
``codex login``, …), so the UI shows the same words the boot-time validator
would, rather than a second copy that drifts.

Results are cached briefly — the chat pane asks on every mount, and
``which`` + ``<cli> --version`` per mount is real latency for an answer that
changes only when the operator installs something.
"""

from __future__ import annotations

import threading
import time

from agent_core_lib.agent_core_lib.data.agent_backend import AgentBackend


# Long enough that tab-switching costs nothing; short enough that an operator
# who installs the CLI and comes back sees the tab go live without a restart.
PROBE_CACHE_SECONDS = 60.0

_LABELS = {
    AgentBackend.CLAUDE.value: 'Claude',
    AgentBackend.CODEX.value: 'Codex',
}

# Backends that offer an interactive chat, in tab order. OpenHands is an API
# client with no session model, so it never gets a chat tab.
CHAT_BACKENDS = (AgentBackend.CLAUDE.value, AgentBackend.CODEX.value)

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def backend_label(backend: str) -> str:
    key = str(backend or '').strip().lower()
    return _LABELS.get(key, key.title() or key)


def _build_probe_client(backend: str, binary: str):
    """A throwaway client wired with nothing but the binary path.

    Only ``validate_connection`` is called on it, and that reads the binary
    and nothing else — so none of the per-task config (model, tools, effort)
    is needed, and asking for it would make readiness depend on a fully
    resolved task config the chat pane does not have.
    """
    if backend == AgentBackend.CODEX.value:
        from codex_core_lib.codex_core_lib.cli_client import CodexCliClient
        return CodexCliClient(binary=binary or 'codex')
    if backend == AgentBackend.CLAUDE.value:
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
        return ClaudeCliClient(binary=binary or 'claude')
    return None


def probe_backend(backend: str, *, binary: str = '', now=None) -> dict:
    """``{'id', 'label', 'ready', 'error'}`` for one backend.

    Never raises: a probe that blows up in an unexpected way is reported as
    not-ready with the exception text, because the alternative is a 500 on
    the endpoint that draws the chat tabs.
    """
    key = str(backend or '').strip().lower()
    clock = now or time.monotonic
    stamp = clock()
    with _lock:
        cached = _cache.get(key)
        if cached and (stamp - cached[0]) < PROBE_CACHE_SECONDS:
            return dict(cached[1])

    result = {'id': key, 'label': backend_label(key), 'ready': False, 'error': ''}
    try:
        client = _build_probe_client(key, binary)
        if client is None:
            result['error'] = f'unknown agent backend {key!r}'
        else:
            client.validate_connection()
            result['ready'] = True
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        result['error'] = str(exc).strip() or exc.__class__.__name__

    with _lock:
        _cache[key] = (stamp, dict(result))
    return result


def probe_chat_backends(binaries: dict | None = None) -> list[dict]:
    """Readiness for every chat backend, in tab order."""
    resolved = binaries or {}
    return [
        probe_backend(backend, binary=str(resolved.get(backend, '') or ''))
        for backend in CHAT_BACKENDS
    ]


def reset_probe_cache() -> None:
    """Drop memoised results — used by tests and after a settings save."""
    with _lock:
        _cache.clear()
