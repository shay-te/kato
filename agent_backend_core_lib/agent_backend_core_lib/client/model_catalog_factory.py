"""What models and effort levels the configured backend offers.

The same shape as the client factory next door, for the same reason: which
backend answers is a routing question, and routing questions belong in the lib
that already knows the backends. Callers ask for a catalog and get one — they
never test a binary name to decide which module to import.

Every function here is best-effort and never raises: these feed the operator's
model and effort pickers, and a picker that fails to render is worse than one
showing a stale-but-sane list. Imports of the transports are lazy and inside
the functions, the same sanctioned pattern the client factory uses — importing
all three eagerly would pull every transport into a process that needs one.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from agent_backend_core_lib.agent_backend_core_lib.client.agent_client_factory import (
    resolve_platform,
)
from agent_backend_core_lib.agent_backend_core_lib.platform import AgentPlatform


def platform_for_binary(binary: str) -> AgentPlatform:
    """The backend a CLI binary path belongs to.

    Callers hold a configured binary (``claude``, ``/usr/local/bin/codex``),
    not a platform name, so the match is on the containing path rather than an
    exact alias. An unrecognised binary answers CLAUDE — the historical
    default, and the one whose fallbacks are always populated.
    """
    name = str(binary or '').strip().lower()
    for platform in (AgentPlatform.CODEX, AgentPlatform.OPENHANDS):
        if platform.value in name:
            return platform
    return AgentPlatform.CLAUDE


def discover_models(platform: AgentPlatform | str, *, force: bool = False) -> list[dict]:
    """The model list for ``platform``'s picker; falls back, never raises.

    Every transport exposes the same ``helpers.model_catalog`` module with the
    same ``discover_models``/``FALLBACK_MODELS`` names, so this dispatches by
    module path rather than by branching per backend — a new transport that
    follows the shape needs one table row, not an ``if``.

    ``force`` bypasses a backend's discovery cache so a just-installed CLI's
    labels appear without waiting out the TTL or restarting the host.
    """
    resolved = _as_platform(platform)
    module = _helper_module(resolved, 'model_catalog')
    if module is not None:
        try:
            models = module.discover_models(force=force)
        except Exception:
            models = []
        if models:
            return [dict(model) for model in models]
    return fallback_models(resolved)


def fallback_models(platform: AgentPlatform | str) -> list[dict]:
    """A sane static list, so the picker always renders."""
    module = _helper_module(_as_platform(platform), 'model_catalog')
    fallback = getattr(module, 'FALLBACK_MODELS', ()) if module else ()
    if not fallback:
        from claude_core_lib.claude_core_lib.helpers.model_catalog import (
            FALLBACK_MODELS,
        )
        fallback = FALLBACK_MODELS
    return [dict(model) for model in fallback]


def discover_effort_levels(
    platform: AgentPlatform | str, binary: str = '',
) -> tuple[str, ...]:
    """The effort levels ``platform`` advertises, or its fallback set.

    Never empty: an empty picker reads to the operator as "this backend has no
    effort control", which is not what a missing discovery source means.
    """
    resolved = _as_platform(platform)
    module = _helper_module(resolved, 'effort_levels')
    if module is not None:
        try:
            levels = tuple(module.discover_effort_levels(binary) if binary
                           else module.discover_effort_levels())
        except Exception:
            levels = ()
        if levels:
            return levels
        fallback = tuple(getattr(module, 'FALLBACK_EFFORT_LEVELS', ()))
        if fallback:
            return fallback
    from claude_core_lib.claude_core_lib.helpers.effort_levels import (
        FALLBACK_EFFORT_LEVELS,
    )
    return tuple(FALLBACK_EFFORT_LEVELS)


# Where each backend keeps the helper modules every transport exposes.
# OPENHANDS is absent on purpose: it is an API client, not a CLI one, and
# ships none of these — it falls through to the shared fallbacks.
_HELPER_PACKAGES = {
    AgentPlatform.CLAUDE: 'claude_core_lib.claude_core_lib.helpers',
    AgentPlatform.CODEX: 'codex_core_lib.codex_core_lib.helpers',
}


def _helper_module(platform: AgentPlatform, name: str):
    """Import ``<backend>.helpers.<name>``, or ``None`` when it has none.

    Lazy and per-call: importing all three transports eagerly would pull every
    CLI's dependencies into a process that needs one.
    """
    package = _HELPER_PACKAGES.get(platform)
    if not package:
        return None
    try:
        return import_module(f'{package}.{name}')
    except Exception:
        return None


def _as_platform(platform: Any) -> AgentPlatform:
    if isinstance(platform, AgentPlatform):
        return platform
    try:
        return resolve_platform(str(platform or ''))
    except ValueError:
        return AgentPlatform.CLAUDE
