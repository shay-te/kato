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
_UPGRADE_TIMEOUT_SECONDS = 300
_VERSION_RE = re.compile(r'(\d+)\.(\d+)\.(\d+)')
_CLAUDE_NPM_PACKAGE = '@anthropic-ai/claude-code'
# Claude Code release where multi-agent Workflows + the ``ultracode`` keyword
# are supported. Env-overridable (KATO_CLAUDE_MIN_VERSION).
_CLAUDE_MIN_DEFAULT = '2.1.160'
# Official install/upgrade pages (env-overridable) the out-of-date banner
# links to. Confirmed canonical: code.claude.com/docs/en/setup,
# developers.openai.com/codex.
_CLAUDE_DOWNLOAD_DEFAULT = 'https://code.claude.com/docs/en/setup'
_CODEX_DOWNLOAD_DEFAULT = 'https://developers.openai.com/codex/'


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


def _download_url(backend: str, env: dict) -> str:
    if backend == 'claude':
        return (env.get('KATO_CLAUDE_DOWNLOAD_URL', '') or _CLAUDE_DOWNLOAD_DEFAULT).strip()
    if backend == 'codex':
        return (env.get('KATO_CODEX_DOWNLOAD_URL', '') or _CODEX_DOWNLOAD_DEFAULT).strip()
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
        'supports_workflows': False, 'download_url': '',
        'can_upgrade': False, 'upgrade_command': '', 'detail': '',
        'upgrade_blocked_reason': '',
    }
    if backend == 'openhands':
        info['detail'] = 'OpenHands runs as a server — no local CLI to version-check.'
        return info

    binary = _binary_for(backend, env)
    info['binary'] = binary
    info['download_url'] = _download_url(backend, env)
    found, raw = _probe(binary, runner=runner)
    info['found'] = found
    info['version_raw'] = raw
    if not found:
        info['up_to_date'] = False
        info['detail'] = f'{binary} not found on PATH'
        _apply_upgrade_flags(info, env)
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
    _apply_upgrade_flags(info, env)
    return info


def _is_truthy(value) -> bool:
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def _is_falsy(value) -> bool:
    return str(value or '').strip().lower() in ('0', 'false', 'no', 'off', 'disabled')


def upgrade_command_str() -> str:
    """The exact command an in-app upgrade runs (shown to the operator for
    approval). Fixed — never built from user input."""
    return f'npm install -g {_CLAUDE_NPM_PACKAGE}@latest'


def upgrade_allowed(env: dict | None = None) -> tuple[bool, str]:
    """``(allowed, reason)`` for an in-app CLI upgrade. Available by default for
    the claude CLI on the host; an operator can hard-disable it with
    ``KATO_ALLOW_CLI_UPGRADE=false``. Not offered in Docker (the CLI lives in
    the image — rebuild it with ``kato sandbox build``). The per-use confirm in
    the UI (showing the exact command) is the approval gate."""
    env = os.environ if env is None else env
    if _is_falsy(env.get('KATO_ALLOW_CLI_UPGRADE')):
        return False, 'in-app upgrade is disabled (KATO_ALLOW_CLI_UPGRADE=false)'
    backend = _resolve_backend(env)
    if backend != 'claude':
        return False, (
            f'in-app upgrade supports only the claude CLI (backend is {backend}) '
            '— use the download page'
        )
    if _is_truthy(env.get('KATO_CLAUDE_DOCKER')):
        return False, (
            'Docker sandbox mode — the CLI is in the image; rebuild with '
            '`kato sandbox build`'
        )
    return True, ''


def _apply_upgrade_flags(info: dict, env: dict) -> None:
    allowed, reason = upgrade_allowed(env)
    info['can_upgrade'] = bool(allowed and not info['up_to_date'])
    info['upgrade_command'] = upgrade_command_str() if info['can_upgrade'] else ''
    # Why one-click upgrade isn't offered — only when there IS an update we
    # can't apply in-app (Docker image, codex backend, or hard-disabled), so
    # the banner can tell the operator what to do instead of going silent.
    info['upgrade_blocked_reason'] = (
        reason if (not info['up_to_date'] and not allowed) else ''
    )


def _default_upgrade_runner(cmd: list) -> tuple[int, str]:
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding='utf-8',
        errors='replace', check=False, timeout=_UPGRADE_TIMEOUT_SECONDS,
    )
    return result.returncode, ((result.stdout or '') + (result.stderr or '')).strip()


def upgrade_agent_cli(env: dict | None = None, runner=None) -> dict:
    """Run the gated, FIXED upgrade command for the claude CLI on the host.

    Caller is responsible for the operator's per-use approval (the UI confirm);
    this enforces the server-side gate (``upgrade_allowed``) + runs ONLY the
    fixed command. Returns ``{ok, message, output, version_before,
    version_after}``. Never raises.
    """
    env = os.environ if env is None else env
    allowed, reason = upgrade_allowed(env)
    if not allowed:
        return {'ok': False, 'message': reason, 'output': '',
                'version_before': None, 'version_after': None}
    before = agent_version_info(env).get('version')
    npm = shutil.which('npm')
    if not npm:
        return {'ok': False, 'message': 'npm not found on PATH — install Node.js/npm',
                'output': '', 'version_before': before, 'version_after': None}
    cmd = [npm, 'install', '-g', f'{_CLAUDE_NPM_PACKAGE}@latest']
    run = runner or _default_upgrade_runner
    try:
        code, output = run(cmd)
    except Exception as exc:
        return {'ok': False, 'message': f'upgrade failed to run: {exc}',
                'output': '', 'version_before': before, 'version_after': None}
    after = agent_version_info(env).get('version')
    return {
        'ok': code == 0,
        'message': ('upgraded — new tasks & chats use it immediately; an '
                    'in-flight chat applies it on its next start (no kato '
                    'restart needed)' if code == 0
                    else f'npm exited with code {code}'),
        'output': output,
        'version_before': before,
        'version_after': after,
    }
