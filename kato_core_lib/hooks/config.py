"""Parse and validate the operator's hooks.json config.

The config file is OPTIONAL — its absence is a no-op (kato runs
without hooks, same as before this module landed). When present,
schema errors raise ``HookConfigError`` at load time so the
operator sees the problem at boot rather than silently dropping
hooks at run time.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class HookPoint(str, Enum):
    """Lifecycle points where hooks can fire.

    String values match the JSON config keys. Kept as a frozen
    enum so a typo in the config (``pre_tool``) is caught at
    parse time rather than silently ignored.
    """
    SESSION_START = 'session_start'
    SESSION_END = 'session_end'
    PRE_TOOL_USE = 'pre_tool_use'
    POST_TOOL_USE = 'post_tool_use'
    USER_PROMPT_SUBMIT = 'user_prompt_submit'
    STOP = 'stop'


_KNOWN_POINTS = frozenset(p.value for p in HookPoint)


class HookConfigError(ValueError):
    """Raised when hooks.json is present but malformed."""


@dataclass(frozen=True)
class HookDefinition(object):
    """One hook entry: when it fires + what shell command runs.

    ``match`` is a dict of ``{field_name: pattern}`` predicates.
    A hook fires only when EVERY predicate matches the event the
    runner is firing on. Patterns:

      - Plain string → equality against the event's value of
        that field (case-sensitive). Use ``tool=Bash`` to gate
        on tool name.
      - Compound key ``<field>_regex`` → regex search against
        the field's value (e.g. ``command_regex=^rm -rf``).

    Without ``match`` the hook fires on every event at its point.
    """

    point: HookPoint
    command: str
    match: dict = field(default_factory=dict)
    timeout_seconds: float = 30.0

    def matches(self, event: dict) -> bool:
        """True when ``event`` satisfies every predicate in ``self.match``.

        Empty / missing match dict → always True.
        """
        if not self.match:
            return True
        for raw_key, predicate in self.match.items():
            key = str(raw_key)
            if key.endswith('_regex'):
                field_name = key[:-len('_regex')]
                target = str(event.get(field_name, '') or '')
                try:
                    if not re.search(str(predicate), target):
                        return False
                except re.error:
                    # Invalid regex in operator config → treat as
                    # non-match so a typo doesn't fire every event
                    # unconditionally.
                    return False
            else:
                target = event.get(key, '')
                if str(target) != str(predicate):
                    return False
        return True


@dataclass(frozen=True)
class HookConfig(object):
    """Whole-file config: hooks grouped by lifecycle point."""

    hooks_by_point: dict[HookPoint, tuple[HookDefinition, ...]]

    @classmethod
    def empty(cls) -> 'HookConfig':
        return cls(hooks_by_point={})

    def for_point(self, point: HookPoint) -> tuple[HookDefinition, ...]:
        return self.hooks_by_point.get(point, ())

    def is_empty(self) -> bool:
        return all(not hooks for hooks in self.hooks_by_point.values())


_DEFAULT_PATH_ENV = 'KATO_HOOKS_CONFIG'
_DEFAULT_PATH = Path.home() / '.kato' / 'hooks.json'


def _resolve_path(explicit_path: str | os.PathLike | None) -> Path | None:
    """Pick the file we should read, or ``None`` if no hooks are configured.

    Precedence: explicit arg → ``KATO_HOOKS_CONFIG`` env var →
    default ``~/.kato/hooks.json``. Returns ``None`` when none of
    those paths exist on disk so callers can short-circuit.
    """
    if explicit_path:
        path = Path(explicit_path)
        return path if path.exists() else None
    env_path = os.environ.get(_DEFAULT_PATH_ENV, '').strip()
    if env_path:
        path = Path(env_path)
        return path if path.exists() else None
    return _DEFAULT_PATH if _DEFAULT_PATH.exists() else None


def _parse_one_hook(raw: object, point: HookPoint) -> HookDefinition:
    if not isinstance(raw, dict):
        raise HookConfigError(
            f'hook entry under {point.value!r} must be an object, got {type(raw).__name__}',
        )
    command = raw.get('command')
    if not isinstance(command, str) or not command.strip():
        raise HookConfigError(
            f'hook entry under {point.value!r} requires a non-empty ``command`` string',
        )
    match = raw.get('match') or {}
    if not isinstance(match, dict):
        raise HookConfigError(
            f'``match`` in hook entry under {point.value!r} must be an object',
        )
    timeout = raw.get('timeout_seconds', 30.0)
    try:
        timeout_float = float(timeout)
    except (TypeError, ValueError):
        raise HookConfigError(
            f'``timeout_seconds`` in hook entry under {point.value!r} must be a number, got {timeout!r}',
        )
    if timeout_float <= 0:
        raise HookConfigError(
            f'``timeout_seconds`` must be positive, got {timeout_float}',
        )
    return HookDefinition(
        point=point,
        command=command.strip(),
        match=dict(match),
        timeout_seconds=timeout_float,
    )


def load_hooks_config(
    path: str | os.PathLike | None = None,
) -> HookConfig:
    """Load and validate the hooks config. Returns an empty config
    when no file exists.

    Raises ``HookConfigError`` on schema problems (unknown hook
    point, missing ``command``, non-positive timeout) so operators
    see the bug at boot.
    """
    resolved = _resolve_path(path)
    if resolved is None:
        return HookConfig.empty()
    try:
        text = resolved.read_text(encoding='utf-8')
    except OSError as exc:
        raise HookConfigError(
            f'failed to read hooks config at {resolved}: {exc}',
        ) from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HookConfigError(
            f'hooks config at {resolved} is not valid JSON: {exc}',
        ) from exc
    if not isinstance(payload, dict):
        raise HookConfigError(
            f'hooks config at {resolved} must be a JSON object at the top level',
        )
    by_point: dict[HookPoint, list[HookDefinition]] = {}
    for raw_point, raw_hooks in payload.items():
        if raw_point not in _KNOWN_POINTS:
            raise HookConfigError(
                f'unknown hook point {raw_point!r} in {resolved}; '
                f'supported points: {sorted(_KNOWN_POINTS)}',
            )
        point = HookPoint(raw_point)
        if not isinstance(raw_hooks, list):
            raise HookConfigError(
                f'hook point {raw_point!r} in {resolved} must map to a list of entries',
            )
        by_point[point] = [
            _parse_one_hook(entry, point) for entry in raw_hooks
        ]
    return HookConfig(
        hooks_by_point={
            point: tuple(hooks) for point, hooks in by_point.items()
        },
    )
