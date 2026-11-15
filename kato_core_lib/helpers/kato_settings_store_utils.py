"""Operator settings persisted to ``~/.kato/settings.json``.

This is the source of truth for everything the planning-UI Settings
drawer edits — repository root, task provider + its credentials, git
host credentials. It lives in the user's home ``.kato`` directory
(next to ``workspaces/`` and ``sessions/``) so it's per-operator, survives ``git clean``, and is
shared across multiple kato checkouts.

Precedence (resolved at boot by ``load_kato_settings_into_environ``):

    real shell env  >  ~/.kato/settings.json

* A variable the operator exported in their shell always wins — an
  emergency override stays in their hands.
* ``settings.json`` is authoritative for everything else. It is the
  ONLY config file kato reads — ``<repo>/.env`` support was removed
  entirely (the first-run wizard + Settings drawer replaced it).

The file is a flat ``{"KEY": "value"}`` JSON object — same env-var
keys kato already reads via hydra ``${oc.env:...}``. Flat (not
nested per-provider) so the boot loader is a trivial dict→environ
copy and the file diffs cleanly.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json
from kato_core_lib.helpers.kato_paths_utils import kato_home_path


_SETTINGS_PATH_ENV_KEY = 'KATO_SETTINGS_FILE'

# write_kato_settings() is read-modify-write against the whole file — two
# concurrent saves (two Settings tabs, or two sections auto-saving near
# simultaneously) can both read the old file before either writes,
# silently reverting one save's keys. Mirrors tool_decision_store.py's
# pattern.
_lock = threading.Lock()


def kato_settings_path() -> Path:
    """``~/.kato/settings.json`` (or ``$KATO_SETTINGS_FILE`` override).

    The env override exists for tests + for operators who keep their
    ``.kato`` dir somewhere non-standard.
    """
    return kato_home_path('settings.json', env_key=_SETTINGS_PATH_ENV_KEY)


def read_kato_settings() -> dict[str, str]:
    """Return the settings dict, or ``{}`` when the file is absent.

    Tolerant of a corrupt / partial file (returns ``{}`` rather than
    raising) so a hand-edit typo can't brick the boot path — the
    operator just falls back to ``.env`` until they fix or re-save.
    """
    path = kato_settings_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Coerce every value to str — env vars are strings, and a
    # hand-edit that put a number / bool shouldn't blow up the
    # ``os.environ[...] = value`` assignment at boot.
    return {str(k): str(v) for k, v in data.items() if k}


def live_setting(key: str, env: dict | None = None) -> str:
    """One setting read FRESH: settings.json → shell env (``env``) → ``''``.

    For the settings the Settings drawer applies without a restart: the file
    is what a save writes, so reading it on every use is what makes the save
    take effect. A corrupt file must not decide the value — it falls through
    to the environment. Returned stripped; blank means unset.
    """
    try:
        settings = read_kato_settings()
    except Exception:  # noqa: BLE001 - a corrupt settings file must not decide it
        settings = {}
    for source in (settings, os.environ if env is None else env):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def write_kato_settings(updates: dict[str, str]) -> dict[str, str]:
    """Merge ``updates`` into the settings file. Returns the new full dict.

    Atomic: read → merge → write a sibling temp file → ``os.replace``
    so a concurrent reader (the boot loader, another webserver
    thread) sees either the old file in full or the new one in full,
    never a torn JSON. Creates ``~/.kato`` if missing. Keys whose
    value is the empty string are kept (kato treats empty as "unset"
    via the ``${oc.env:KEY,"default"}`` pattern) so the operator can
    explicitly clear a field.
    """
    if not updates:
        return read_kato_settings()
    with _lock:
        current = read_kato_settings()
        current.update({str(k): str(v) for k, v in updates.items() if k})
        path = kato_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Route through the shared atomic writer (sibling tmp + os.replace)
        # but keep settings-specific behavior: surface OSError to the
        # operator (the UI relies on it) and preserve the trailing
        # newline for clean diffs.
        atomic_write_json(
            path,
            current,
            trailing_newline=True,
            raise_on_error=True,
        )
        return current


def _settings_with_resolved_credentials() -> dict[str, str]:
    """``read_kato_settings()`` plus any token resolved from a credential
    SOURCE (``<PROVIDER>_API_TOKEN_SOURCE``) instead of a pasted value.

    The "is kato configured yet" check reads it; the boot gets the same tokens
    from ``load_resolved_credentials_into_environ`` (the same resolver), so the
    two can never disagree about whether a provider has a usable token. Import is local: credential_sources
    shells out to ``gh``/``git``, and nothing should pay for that just by
    importing the settings store.
    """
    settings = read_kato_settings()
    try:
        from kato_core_lib.helpers.credential_sources import resolved_credential_env
        settings.update(resolved_credential_env(settings))
    except Exception:  # noqa: BLE001 - discovery must never break config load
        pass
    return settings


def config_env_with_form(form: Mapping[str, str]) -> dict[str, str]:
    """``effective_config_env()`` with a settings form's values on top, as if
    they were saved — so a connection can be tested BEFORE Save. A filled field
    replaces; a cleared one is dropped, so its default applies (an empty value
    passed on would kill the default instead). A credential source picked on
    the form is resolved the way boot resolves one."""
    env = effective_config_env()
    for key, value in form.items():
        text = str(value or '')
        if text.strip():
            env[key] = text
        else:
            env.pop(key, None)
    try:
        from kato_core_lib.helpers.credential_sources import resolved_credential_env
        env.update(resolved_credential_env(env))
    except Exception:  # noqa: BLE001 - discovery must never break the test
        pass
    return env


def load_resolved_credentials_into_environ() -> list[str]:
    """Put every token a credential SOURCE resolves to into ``os.environ``.

    A provider set to "use my git login" / "use my gh login"
    (``<PROVIDER>_API_TOKEN_SOURCE``) has no token in settings.json — only the
    source. The boot exported settings.json as it is, so the clients never got
    a token: Test connection (which resolves the source) said "Connected" while
    every pull request failed with "missing pull request API token" (KAN-1,
    2026-11-15). Every boot calls this first (``main``); a token already in the
    process env — exported in the shell, pasted in Settings — wins.

    Returns the keys it set (never the tokens). Never raises.
    """
    merged = {**read_kato_settings(), **{k: v for k, v in os.environ.items() if v}}
    try:
        from kato_core_lib.helpers.credential_sources import resolved_credential_env
        resolved = resolved_credential_env(merged)
    except Exception:  # noqa: BLE001 - discovery must never break the boot
        return []
    keys = []
    for key, token in resolved.items():
        if token and not os.environ.get(key):
            os.environ[key] = token
            keys.append(key)
    return keys


def load_kato_settings_into_environ() -> int:
    """Inject ``~/.kato/settings.json`` into ``os.environ``, then the tokens
    its credential sources resolve to (``load_resolved_credentials_into_environ``).

    A key the process env holds a value for wins. A key it has EMPTY is
    unset: everything else (the validators, the settings UI,
    ``effective_config_env``) treats empty as unset, so a blank var inherited
    at spawn must not shadow the value the operator saved. Returns how many
    keys it set.

    EMPTY VALUES ARE NOT EXPORTED. ``write_kato_settings`` keeps a cleared
    key in the file on purpose, but omegaconf resolves a set-but-empty var to
    ``''`` and never reaches its ``${oc.env:KEY,"default"}`` default: a
    cleared ``YOUTRACK_PROGRESS_STATE_FIELD`` once resolved to ``''`` instead
    of ``"State"`` and no task could start.
    """
    added = 0
    for key, value in read_kato_settings().items():
        if value and not os.environ.get(key):
            os.environ[key] = value
            added += 1
    return added + len(load_resolved_credentials_into_environ())


def effective_config_env() -> dict[str, str]:
    """The config env kato would boot with RIGHT NOW, as one mapping.

    Merges the two stores with boot precedence — live ``os.environ``
    > ``~/.kato/settings.json`` — where only NON-EMPTY values override
    lower layers (an empty value falls through, mirroring how the
    settings UI resolves a single key).

    This is the single source of truth for "is kato configured yet":
    the webserver's ``/api/config-status`` endpoint and the setup-mode
    boot poll in ``main`` both feed it to ``collect_config_errors``,
    so a value saved through the Settings UI counts immediately —
    without a restart — and the two checks can never drift apart.
    """
    merged: dict[str, str] = {}
    for source in (_settings_with_resolved_credentials(), dict(os.environ)):
        for key, value in source.items():
            if value:
                merged[key] = value
    return merged
