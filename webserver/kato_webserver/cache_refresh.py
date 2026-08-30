"""Explicit cache refreshes, behind a POST.

Three discovery caches used to be droppable with ``?refresh=1`` on their GET
routes. Refreshing any of them is an ACTION, not a read: it spawns a CLI
subprocess, calls the npm registry or the Anthropic models API, and — for the
backend probe — clears a cache that is global to the whole process, so one
client's refresh invalidates it for every other client.

That is a poor fit for GET regardless of who can reach it. Browsers and
proxies are free to issue GETs nobody asked for: Chrome prefetches links on
hover, previewers fetch URLs to render a card. A verb that promises "this only
reads" should not be the one that respawns a subprocess.

(Not a security fix. Every route already sits behind the same origin guard —
``_register_csrf_guard`` covers GET exactly as it covers POST — so this
changes nothing about who can call it, only about what can call it by
accident.)

DELIBERATELY SELF-CONTAINED. Everything the feature needs is here: the target
names, what refreshing each one means, and the dispatch. To remove it, delete
this file, drop the ``/api/refresh`` route and the two lines that import this,
and put ``?refresh=1`` back on the three GET handlers.
"""

from __future__ import annotations

import logging

#: Public target names, as the client sends them.
AGENT_VERSION = 'agent-version'
AGENT_BACKENDS = 'agent-backends'
MODELS = 'models'


def _refresh_agent_version(app) -> None:
    """Drop the per-backend version probe AND the published-version lookup.

    Both, or a release published during this process's lifetime stays
    invisible until the registry TTL lapses — which is exactly the case the
    operator hits Refresh for.
    """
    for key in [k for k in list(app.config) if str(k).startswith('AGENT_VERSION_INFO')]:
        app.config.pop(key, None)
    from kato_core_lib.helpers.agent_version_utils import reset_latest_version_cache
    reset_latest_version_cache()


def _refresh_agent_backends(app) -> None:
    """Drop the CLI-readiness probe.

    Process-global by nature (``agent_backend_readiness`` memoises at module
    level), so this refresh is visible to every client, not just the caller.
    """
    from kato_core_lib.helpers.agent_backend_readiness import reset_probe_cache
    reset_probe_cache()


def _refresh_models(app) -> None:
    """Re-probe the model catalogue, bypassing its discovery TTL.

    The factory has no reset — ``force`` re-probes and REPLACES the cached
    entry — so refreshing means doing the discovery now rather than clearing
    and waiting for the next reader. The binary is resolved off the runner
    here rather than through app.py's private helper, so this module stays
    free of a circular import and can be deleted on its own.
    """
    from agent_backend_core_lib.agent_backend_core_lib.client.model_catalog_factory import (
        discover_models,
        platform_for_binary,
    )
    runner = app.config.get('PLANNING_SESSION_RUNNER')
    defaults = getattr(runner, '_defaults', None) if runner is not None else None
    binary = str(getattr(defaults, 'binary', '') or 'claude')
    discover_models(platform_for_binary(binary), force=True)


_TARGETS = {
    AGENT_VERSION: _refresh_agent_version,
    AGENT_BACKENDS: _refresh_agent_backends,
    MODELS: _refresh_models,
}

#: Every accepted target, for the route's error message and for tests.
TARGET_NAMES = tuple(sorted(_TARGETS))


def refresh_target(app, target: str) -> tuple[bool, str]:
    """Run one named refresh. Returns ``(ok, error_message)``.

    An unknown target is a client bug and says so; a refresh that RAISES is
    reported but not fatal — a stale cache is a far smaller problem than a
    500 on the button that exists to unstick things.
    """
    name = str(target or '').strip()
    action = _TARGETS.get(name)
    if action is None:
        return False, f'unknown refresh target {name!r}; expected one of {list(TARGET_NAMES)}'
    try:
        action(app)
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        logging.getLogger(__name__).exception('refresh of %s failed', name)
        return False, f'could not refresh {name}: {exc}'
    return True, ''
