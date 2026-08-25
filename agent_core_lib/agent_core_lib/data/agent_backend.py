"""The canonical name of each agent backend.

These strings are written into session records, read from operator config, and
compared in a dozen places across libs that cannot import each other. Spelled
by hand at each site they drift silently: a typo'd ``'cluade'`` compares False
forever, and nothing fails until an operator notices a feature quietly not
applying to their backend.

Lives here because ``agent_core_lib`` is the one base every lib may import —
the transports, the orchestrating host, and the client factory alike. The factory's
own ``AgentPlatform`` derives its values from this enum rather than restating
them, so the two cannot disagree.

``str`` mixin on purpose: these values are PERSISTED (session records) and read
from config, so ``AgentBackend.CLAUDE == 'claude'`` must stay true and
``json.dumps`` must keep writing the bare string.
"""

from __future__ import annotations

from enum import Enum


class AgentBackend(str, Enum):
    """An agent transport the host can run."""

    CLAUDE = 'claude'
    CODEX = 'codex'
    OPENHANDS = 'openhands'

    @classmethod
    def parse(cls, value: object) -> 'AgentBackend | None':
        """The backend ``value`` names, or ``None`` if it names none.

        Tolerates the casing and padding that arrive from config files and
        env vars. ``None`` rather than a default, because guessing a backend
        is how a feature silently applies to the wrong CLI.
        """
        if isinstance(value, cls):
            # A member already IS the answer. Not a shortcut: ``str()`` on a
            # str-mixin Enum yields 'AgentBackend.CLAUDE', not 'claude', so
            # stringifying a member here would fail to parse its own type.
            return value
        key = str(value or '').strip().lower()
        for backend in cls:
            if backend.value == key:
                return backend
        return None

    @classmethod
    def is_a(cls, value: object, backend: 'AgentBackend') -> bool:
        """Does ``value`` name ``backend``? Normalized, never raises."""
        return cls.parse(value) is backend
