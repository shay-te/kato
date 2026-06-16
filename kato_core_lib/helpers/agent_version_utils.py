"""Detect the CONFIGURED agent CLI's version + capabilities.

So the UI can (a) warn when the agent CLI is out of date and (b) hide
features the installed CLI can't actually run (e.g. the ``ultracode`` /
multi-agent-workflow toggle — older Claude CLIs treat ``ultracode`` as plain
text, so showing the toggle is misleading).

``KATO_AGENT_BACKEND`` decides which binary to probe — claude →
``KATO_CLAUDE_BINARY``, codex → ``KATO_CODEX_BINARY``, openhands → no local
CLI. Minimum-recommended versions are env-overridable so operators on the
cutting edge can adjust without a code change; the built-in default tracks the
Claude Code release where Workflows + ``ultracode`` work (confirm against the
changelog and bump via ``KATO_CLAUDE_MIN_VERSION`` as needed).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

_PROBE_TIMEOUT_SECONDS = 15
_VERSION_RE = re.compile(r'(\d+)\.(\d+)\.(\d+)')
# Claude Code release where multi-agent Workflows + the ``ultracode`` keyword
# are supported. Env-overridable (KATO_CLAUDE_MIN_VERSION).
_CLAUDE_MIN_DEFAULT = '2.1.160'


def parse_version(text) -> tuple[int, int, int] | None:
    """First ``MAJOR.MINOR.PATCH`` found in ``text`` (e.g. CLI ``--version``)."""
    match = _VERSION_RE.search(str(text or ''))
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _resolve_backend(env: dict) -> str:
    name = (env.get('KATO_AGENT_BACKEND', '') or '').strip()
    try:
        from agent_backend_core_lib.agent_backend_core_lib.client.agent_client_factory import (
            resolve_platform,
        )
        return resolve_platform(name).value
    except Exception:
        low = name.lower()
        if low.startswith('claude'):
            return 'claude'
        if 'codex' in low:
            return 'codex'
        return 'openhands'


def _binary_for(backend: str, env: dict) -> str:
    if backend == 'claude':
        return env.get('KATO_CLAUDE_BINARY', '').strip() or 'claude'
    if backend == 'codex':
        return env.get('KATO_CODEX_BINARY', '').strip() or 'codex'
    return ''


def _min_version(backend: str, env: dict) -> str:
    if backend == 'claude':
        return (env.get('KATO_CLAUDE_MIN_VERSION', '') or _CLAUDE_MIN_DEFAULT).strip()
    if backend == 'codex':
        return (env.get('KATO_CODEX_MIN_VERSION', '') or '').strip()
    return ''


def _probe(binary: str, runner=None) -> tuple[bool, str]:
    """``(found, raw_output)`` for ``<binary> --version``. ``found`` is False
    only when the binary isn't on PATH; a probe that runs but fails returns
    ``(True, '')``. ``runner`` is injectable for tests."""
    path = shutil.which(binary)
    if not path:
        return False, ''
    run = runner or _default_runner
    try:
        return True, run(path)
    except Exception:
        return True, ''


def _default_runner(path: str) -> str:
    result = subprocess.run(
        [path, '--version'], capture_output=True, text=True,
        encoding='utf-8', errors='replace', check=False,
        timeout=_PROBE_TIMEOUT_SECONDS,
    )
    return (result.stdout or result.stderr or '').strip()


def agent_version_info(env: dict | None = None, runner=None) -> dict:
    """Report the configured backend's CLI version + capability flags.

    Keys: ``backend``, ``binary``, ``found``, ``version`` (``'x.y.z'``/None),
    ``version_raw``, ``recommended_min``, ``up_to_date``,
    ``supports_workflows`` (claude + new enough), ``detail``. Never raises.
    """
    env = os.environ if env is None else env
    backend = _resolve_backend(env)
    info = {
        'backend': backend, 'binary': '', 'found': True, 'version': None,
        'version_raw': '', 'recommended_min': '', 'up_to_date': True,
        'supports_workflows': False, 'detail': '',
    }
    if backend == 'openhands':
        info['detail'] = 'OpenHands runs as a server — no local CLI to version-check.'
        return info

    binary = _binary_for(backend, env)
    info['binary'] = binary
    found, raw = _probe(binary, runner=runner)
    info['found'] = found
    info['version_raw'] = raw
    if not found:
        info['up_to_date'] = False
        info['detail'] = f'{binary} not found on PATH'
        return info

    version = parse_version(raw)
    info['version'] = '.'.join(str(n) for n in version) if version else None
    min_str = _min_version(backend, env)
    info['recommended_min'] = min_str
    min_tuple = parse_version(min_str) if min_str else None
    if version and min_tuple:
        info['up_to_date'] = version >= min_tuple
    elif not version:
        # Unknown version: don't false-alarm the banner, but don't claim
        # workflow support either.
        info['detail'] = 'could not parse the CLI version'
    # Workflows / ultracode are a Claude Code feature — only claude, new enough.
    if backend == 'claude' and version and min_tuple:
        info['supports_workflows'] = version >= min_tuple
    return info
