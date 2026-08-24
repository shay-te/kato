"""The reasoning-effort levels this CLI supports.

Mirrors ``claude_core_lib.helpers.effort_levels`` name for name, so a caller
holding a backend can ask either one the same question. What differs is where
the answer comes from: Codex has no ``--effort`` flag and no help text listing
levels — reasoning depth is routed through the ``model_reasoning_effort`` key
in ``~/.codex/config.toml``. There is therefore nothing to discover from the
binary, and the supported set is the static one below.

That is a real difference, not a missing feature: probing ``codex --help`` for
an ``--effort`` flag would always come back empty, and an empty picker reads
to the operator as "this backend has no effort control", which is wrong.
"""

from __future__ import annotations

# The levels Codex accepts for ``model_reasoning_effort``. Static because the
# CLI publishes no discovery for them — see the module docstring.
FALLBACK_EFFORT_LEVELS = ('low', 'medium', 'high', 'xhigh', 'max')


def discover_effort_levels(binary: str = 'codex', timeout: float = 10.0) -> list[str]:
    """The effort levels this CLI supports. Never empty, never raises.

    ``binary`` and ``timeout`` are accepted for signature parity with the
    other transports and deliberately unused: there is no binary probe to run.
    """
    del binary, timeout  # no discovery source — see the module docstring
    return list(FALLBACK_EFFORT_LEVELS)


def reset_effort_levels_cache() -> None:
    """No-op: nothing is cached, because nothing is discovered.

    Present so a caller (or a CLI-upgrade path) can reset every backend
    uniformly without asking which ones actually cache.
    """
    return None
