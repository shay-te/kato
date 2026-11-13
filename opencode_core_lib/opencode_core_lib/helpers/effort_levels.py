"""The reasoning-effort levels offered for OpenCode.

Mirrors every other transport's ``helpers.effort_levels`` name for name. In
OpenCode an effort is a model VARIANT (``--variant high`` on V1,
``provider/model#high`` on V2), and which variants exist depends on the model
— the CLI publishes no list to discover. So the set is static: the variant
names the common providers use. A model without the picked variant is refused
by OpenCode itself, with its own message.
"""

from __future__ import annotations

FALLBACK_EFFORT_LEVELS = ('minimal', 'low', 'medium', 'high', 'max')


def discover_effort_levels(binary: str = 'opencode', timeout: float = 10.0) -> list[str]:
    """The effort levels offered. Never empty, never raises.

    ``binary`` and ``timeout`` are accepted for signature parity and unused:
    there is no binary probe to run (see the module docstring).
    """
    del binary, timeout
    return list(FALLBACK_EFFORT_LEVELS)


def reset_effort_levels_cache() -> None:
    """No-op: nothing is cached, because nothing is discovered."""
    return None
