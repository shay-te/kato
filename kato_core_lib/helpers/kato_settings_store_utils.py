"""Operator settings persisted to ``~/.kato/settings.json``.

This is the source of truth for everything the planning-UI Settings
drawer edits — repository root, task provider + its credentials, git
host credentials. It lives in the user's home ``.kato`` directory
(next to ``workspaces/`` and ``sessions/``) so it's per-operator, survives ``git clean``, and is
shared across multiple kato checkouts.

Precedence — ONE rule, ``settings_first_env``:

    ~/.kato/settings.json  >  the environment kato was started from

* A value saved in Settings ALWAYS applies. The environment only fills a key
  Settings leaves empty (a Docker deployment with no settings.json, a test).
  It used to be the other way round, and a stale ``.env`` the editor loads
  into every terminal kept an old Bitbucket token in kato's env: the new one
  saved in Settings passed Test connection and was never used, not even after
  a restart (2026-11-15, operator: "no .env, only kato settings").
* ``settings.json`` is the ONLY config file kato reads — ``<repo>/.env``
  support was removed entirely (the first-run wizard + Settings replaced it).
* The ``kato up`` launcher (scripts/_script_utils.layered_env) and the desktop
  sidecar keep stdlib copies of the rule; tests pin all of them equal.

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


def settings_first_env(settings: Mapping[str, str], environ: Mapping[str, str]) -> dict[str, str]:
    """THE precedence rule, as one mapping: a non-empty ``settings`` value wins,
    else a non-empty ``environ`` value; empty everywhere is unset (never ``''``,
    which omegaconf would take over its default).

    A git host whose Settings choose a credential SOURCE ("use my git login")
    gets its token from that source, never from a token the environment
    happens to carry (a stale ``.env``) — and none at all when the source has
    nothing, so the error says to add one instead of a wrong token answering
    401. Import is local: credential_sources shells out to ``gh``/``git``.
    """
    merged = {str(k): str(v) for k, v in environ.items() if v}
    merged.update({str(k): str(v) for k, v in settings.items() if v})
    try:
        from kato_core_lib.helpers.credential_sources import (
            DISCOVERABLE_PROVIDERS,
            SOURCE_PASTED,
            resolved_credential_env,
            source_key,
            token_key,
        )
    except Exception:  # noqa: BLE001 - discovery must never break config load
        return merged
    for provider in DISCOVERABLE_PROVIDERS:
        chosen = str(merged.get(source_key(provider)) or '').strip()
        if chosen and chosen != SOURCE_PASTED and not str(settings.get(token_key(provider)) or '').strip():
            merged.pop(token_key(provider), None)
    try:
        merged.update(resolved_credential_env(merged))
    except Exception:  # noqa: BLE001 - discovery must never break config load
        pass
    return merged


def config_env_with_form(form: Mapping[str, str]) -> dict[str, str]:
    """``effective_config_env()`` with a settings form's values on top, as if
    they were saved — so a connection can be tested BEFORE Save. A filled field
    replaces; a cleared one is dropped, so its default applies (an empty value
    passed on would kill the default instead). The same rule as the boot
    (``settings_first_env``), so what Test connection checks is what kato runs."""
    saved = read_kato_settings()
    for key, value in form.items():
        saved[str(key)] = str(value or '').strip() and str(value)
    environ = {k: v for k, v in os.environ.items() if str(k) not in form}
    return settings_first_env(saved, environ)


def load_kato_settings_into_environ() -> list[str]:
    """Make ``os.environ`` what ``settings_first_env`` says: every non-empty
    saved value, every token a chosen credential source resolves to, and no
    token a chosen source replaced. Every boot calls it first (``main``), and
    the setup-mode switch again once the settings are complete.

    Returns the keys it changed (never a value). Never raises.
    """
    settings = read_kato_settings()
    wanted = settings_first_env(settings, os.environ)
    changed = []
    for key, value in wanted.items():
        if os.environ.get(key) != value:
            os.environ[key] = value
            changed.append(key)
    try:
        from kato_core_lib.helpers.credential_sources import DISCOVERABLE_PROVIDERS, token_key
    except Exception:  # noqa: BLE001
        return changed
    for provider in DISCOVERABLE_PROVIDERS:
        key = token_key(provider)
        if key not in wanted and key in os.environ:
            del os.environ[key]  # an inherited token a chosen source replaced
            changed.append(key)
    return changed


def effective_config_env() -> dict[str, str]:
    """The config env kato would boot with RIGHT NOW, as one mapping
    (``settings_first_env`` over settings.json and the live environment).

    This is the single source of truth for "is kato configured yet":
    the webserver's ``/api/config-status`` endpoint and the setup-mode
    boot poll in ``main`` both feed it to ``collect_config_errors``,
    so a value saved through the Settings UI counts immediately —
    without a restart — and the two checks can never drift apart.
    """
    return settings_first_env(read_kato_settings(), os.environ)
