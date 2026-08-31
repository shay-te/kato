"""List the CLI sessions a backend has on disk, for the adoption picker.

Adoption lets an operator hand a conversation they already started in the
agent's own CLI over to the host, which then ``--resume``\\s it instead of
opening a fresh one. To offer that, something has to enumerate what is on
disk — and where "on disk" is differs per backend: Claude keys transcripts by
an encoded cwd under ``~/.claude/projects``, Codex writes date-foldered
rollouts under ``~/.codex/sessions``.

Each transport already owns that knowledge in its own ``session/index.py``,
and both return the SAME row shape deliberately, so a caller needs the store
layout for none of them — only a way to pick the module. That pick is what
this factory is.

It lives beside ``agent_client_factory`` for the same reason that one does:
this is the sanctioned lazy-import seam. Importing a transport costs its whole
dependency tree, and a host configured for one backend must not pay for the
others just to draw a dropdown. The import therefore happens INSIDE the
function, never at module scope.

A backend with no session store is not an error — ``openhands`` runs sessions
server-side, so there is nothing local to adopt. It returns an empty list, and
the picker simply shows nothing to pick.
"""

from __future__ import annotations

from typing import Any

from agent_backend_core_lib.agent_backend_core_lib.client.agent_client_factory import (
    resolve_platform,
)
from agent_backend_core_lib.agent_backend_core_lib.platform import AgentPlatform


def supports_session_adoption(backend: str) -> bool:
    """Does ``backend`` keep local sessions an operator could adopt?

    False for a backend whose conversations do not live on this machine.
    ``list_adoptable_sessions`` uses it to answer with an empty list instead
    of guessing at a store; a UI can also use it to hide an adoption control
    rather than offer one that could only ever come back empty.
    """
    try:
        platform = resolve_platform(backend)
    except ValueError:
        return False
    return platform in (AgentPlatform.CLAUDE, AgentPlatform.CODEX)


def list_adoptable_sessions(
    backend: str,
    *,
    query: str = '',
    max_results: int = 100,
) -> list[Any]:
    """Rows for ``backend``'s local sessions, most-recently-modified first.

    ``query`` is passed through to the backend's own matcher (cwd and message
    previews, case-insensitive). Rows are that backend's own metadata
    dataclass. Every backend defines the fields a picker draws — id, cwd,
    mtime, turn count, first/last message — so a caller can render any row
    without knowing which backend answered; a backend may carry EXTRA fields
    beyond those (Claude adds ``transcript_path``), so a caller serialising
    rows straight through will emit a slightly different key set per backend.

    An unknown backend returns an empty list rather than raising: this feeds a
    picker, and a bad selector should show "nothing to adopt", not a 500.
    """
    if not supports_session_adoption(backend):
        return []
    platform = resolve_platform(backend)
    if platform is AgentPlatform.CODEX:
        from codex_core_lib.codex_core_lib.session.index import list_sessions
    else:
        from claude_core_lib.claude_core_lib.session.index import list_sessions
    return list(list_sessions(query=query, max_results=max_results))


def requires_transcript_migration(backend: str) -> bool:
    """Must an adopted transcript be COPIED for ``backend`` to resume it?

    Claude resolves a transcript through a directory keyed by the cwd it will
    run in, so adopting a session started elsewhere means placing a snapshot
    under the host's workspace clone — and the snapshot, rather than a move,
    is what keeps the host's git state out of the operator's live checkout.

    Codex resolves a rollout by id across one flat global store, so the file
    is already where ``codex exec resume`` will look. Copying it there would
    at best be a no-op and at worst duplicate a transcript under a second id.
    """
    try:
        return resolve_platform(backend) is AgentPlatform.CLAUDE
    except ValueError:
        return False
