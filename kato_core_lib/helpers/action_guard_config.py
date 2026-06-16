"""Resolve the operator's Action Guard posture into a ``CommandPolicy``.

Kato-side glue: reads the ``KATO_ACTION_GUARD_*`` settings (with the same
store precedence as the rest of kato) and hands the agent-agnostic engine
(``agent_core_lib.command_policy``) a concrete policy. Kept out of the
engine so the engine stays product-free — same injection pattern as
``workspace_refusal_guidance``.

Read FRESH on each resolve so a posture change saved in the Settings UI
(written synchronously to ``~/.kato/settings.json``) takes effect on the
next agent action — no kato restart. ``settings.json`` is the live,
UI-managed source; ``os.environ`` (real shell / ``.env``) is the fallback
when a key was never set in the UI; the engine's secure default applies
when neither has it.
"""

from __future__ import annotations

import os
import sys

from agent_core_lib.agent_core_lib.helpers.command_policy import CommandPolicy
from kato_core_lib.helpers.action_guard_audit import action_guard_audit_path
from kato_core_lib.helpers.kato_settings_schema_utils import (
    ACTION_GUARD_SECURE_DEFAULTS,
    ACTION_GUARD_ENV_PREFIX,
)
from kato_core_lib.helpers.kato_settings_store_utils import read_kato_settings

# Categories where an ``allow`` posture is worth shouting about at boot —
# the antivirus-triggering exfiltration paths + off-machine data flow.
_HIGH_RISK_CATEGORIES = ('credential_read', 'network_exfil', 'network_tool')


def _resolved_value(env_key: str, settings: dict, env: dict) -> str:
    """settings.json (live) → shell/.env → secure default."""
    for source in (settings, env):
        value = source.get(env_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ACTION_GUARD_SECURE_DEFAULTS[env_key]


def action_guard_posture(env: dict | None = None) -> dict[str, str]:
    """Return ``{KATO_ACTION_GUARD_* : resolved value}`` for every key.

    Used by the resolver, the ``/api/all-settings`` default-fill, and the
    boot banner so all three show the SAME posture the engine enforces.
    """
    env = os.environ if env is None else env
    try:
        settings = read_kato_settings()
    except Exception:
        settings = {}
    return {
        env_key: _resolved_value(env_key, settings, env)
        for env_key in ACTION_GUARD_SECURE_DEFAULTS
    }


def resolve_action_guard_policy(env: dict | None = None) -> CommandPolicy:
    """Build the live :class:`CommandPolicy` from the resolved posture.

    Never raises — a misconfiguration falls back to the secure default so a
    bad settings file can never disable the guard or crash the permission
    pipeline.
    """
    try:
        posture = action_guard_posture(env)
        mapping = {
            env_key[len(ACTION_GUARD_ENV_PREFIX):].lower(): value
            for env_key, value in posture.items()
        }
        return CommandPolicy.from_mapping(mapping)
    except Exception:
        return CommandPolicy.secure_default()


def action_guard_posture_lines(env: dict | None = None) -> list[str]:
    """Human-readable posture summary lines (no I/O) for the boot banner
    and ``kato doctor``. The first line is a header; warnings (if any) are
    prefixed ``WARNING:``."""
    posture = action_guard_posture(env)
    enabled = str(
        posture.get('KATO_ACTION_GUARD_ENABLED', 'true'),
    ).strip().lower() != 'false'
    rows: list[tuple[str, str]] = []
    counts = {'block': 0, 'ask': 0, 'allow': 0}
    for env_key, value in posture.items():
        if env_key == 'KATO_ACTION_GUARD_ENABLED':
            continue
        category = env_key[len(ACTION_GUARD_ENV_PREFIX):].lower()
        counts[value] = counts.get(value, 0) + 1
        rows.append((category, value))

    lines = [
        ' kato — Action Guard (Layer B)',
        f'  enabled               : {"true" if enabled else "false"}',
        f'  posture               : block×{counts["block"]} '
        f'ask×{counts["ask"]} allow×{counts["allow"]}',
    ]
    if enabled:
        for category, value in rows:
            lines.append(f'  {category:<21} : {value}')
    lines.append(f'  audit log             : {action_guard_audit_path()}')
    if not enabled:
        lines.append(
            '  WARNING: Action Guard content-aware blocking is OFF — only the '
            'CLI denylist floor + Docker apply.',
        )
    for category, value in rows:
        if value == 'allow' and category in _HIGH_RISK_CATEGORIES:
            lines.append(
                f'  WARNING: {category} posture is ALLOW — the agent may do '
                'this without prompting.',
            )
    return lines


def print_action_guard_posture(
    env: dict | None = None, stderr=None,
) -> None:
    """Write the Action Guard posture to stderr at boot, right after the
    sandbox security banner — so an operator sees, at a glance, exactly what
    the agent is allowed to do."""
    target = stderr if stderr is not None else sys.stderr
    bar = '=' * 78
    body = '\n'.join([bar, *action_guard_posture_lines(env), bar])
    target.write('\n' + body + '\n')
    target.flush()
