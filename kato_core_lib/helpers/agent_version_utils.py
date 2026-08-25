"""Detect the CONFIGURED agent CLI's version + capabilities.

So the UI can (a) tell the operator when a newer agent CLI is available and
(b) hide features the installed CLI can't actually run (e.g. the ``ultracode``
/ multi-agent-workflow toggle — older Claude CLIs treat ``ultracode`` as plain
text, so showing the toggle is misleading).

``KATO_AGENT_BACKEND`` decides which binary to probe — claude →
``KATO_CLAUDE_BINARY``, codex → ``KATO_CODEX_BINARY``, openhands → no local
CLI.

Two INDEPENDENT notions of "old", which used to be conflated into one:

* ``update_available`` — the installed version is behind the version actually
  PUBLISHED right now (read from the npm registry, no auth). This is what
  drives the upgrade offer.
* ``up_to_date`` — the installed version meets the recommended FLOOR
  (``KATO_CLAUDE_MIN_VERSION``, default = the release where Workflows /
  ``ultracode`` work). This gates capability flags.

Conflating them was a real bug: with the floor at 2.1.160, an install on
2.1.179 reported "up to date" and hid the Upgrade button even though 2.1.222
was published — the operator could sit dozens of releases behind and the app
would never say so.
"""

from __future__ import annotations

from agent_core_lib.agent_core_lib.data.agent_backend import AgentBackend
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request


def _config_env() -> dict:
    """The config kato would boot with RIGHT NOW — ``settings.json`` included.

    ``os.environ`` alone is the wrong default here. Every key this module
    reads (``KATO_AGENT_BACKEND``, ``KATO_CLAUDE_BINARY``,
    ``KATO_ALLOW_CLI_UPGRADE``, the min-version floors) is an OPERATOR
    SETTING that lives in ``~/.kato/settings.json``, and saving one does not
    mutate the running process env. That made the version probe disagree with
    ``/api/config-status`` — which already resolves through
    ``effective_config_env`` — so pointing ``KATO_CLAUDE_BINARY`` at an
    absolute path cleared the setup gate while the banner went on reporting
    "claude not found on PATH" until a restart.

    Falls back to the process env if the store can't be read: a version probe
    must degrade, never raise.
    """
    try:
        from kato_core_lib.helpers.kato_settings_store_utils import (
            effective_config_env,
        )
        return effective_config_env()
    except Exception:
        return dict(os.environ)


_PROBE_TIMEOUT_SECONDS = 15
_UPGRADE_TIMEOUT_SECONDS = 300
_VERSION_RE = re.compile(r'(\d+)\.(\d+)\.(\d+)')
_CLAUDE_NPM_PACKAGE = '@anthropic-ai/claude-code'
_CODEX_NPM_PACKAGE = '@openai/codex'
# The npm package each backend's CLI ships as. Both are published to the public
# registry, so "what is the latest version" needs no credential.
_NPM_PACKAGES = {'claude': _CLAUDE_NPM_PACKAGE, 'codex': _CODEX_NPM_PACKAGE}
_NPM_REGISTRY_URL = 'https://registry.npmjs.org/{package}/latest'
_REGISTRY_TIMEOUT_SECONDS = 4.0
# A published release doesn't change minute to minute; cache so the banner's
# probe doesn't hit the registry on every page load, but re-check often enough
# that a same-day release surfaces without a restart.
_LATEST_TTL_SECONDS = 1800.0
# Claude Code release where multi-agent Workflows + the ``ultracode`` keyword
# are supported. Env-overridable (KATO_CLAUDE_MIN_VERSION).
_CLAUDE_MIN_DEFAULT = '2.1.160'
# Official install/upgrade pages (env-overridable) the out-of-date banner
# links to. Confirmed canonical: code.claude.com/docs/en/setup,
# developers.openai.com/codex.
_CLAUDE_DOWNLOAD_DEFAULT = 'https://code.claude.com/docs/en/setup'
_CODEX_DOWNLOAD_DEFAULT = 'https://developers.openai.com/codex/'

_latest_cache: dict[str, tuple[float, str | None]] = {}
_latest_lock = threading.Lock()


def parse_version(text) -> tuple[int, int, int] | None:
    """First ``MAJOR.MINOR.PATCH`` found in ``text`` (e.g. CLI ``--version``)."""
    match = _VERSION_RE.search(str(text or ''))
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _resolve_backend(env: dict, backend: str = '') -> str:
    """The backend to report on: an explicit ask, else the configured one.

    ``backend`` is what the operator is LOOKING at. Every task now shows a
    tab per agent, so "is my CLI out of date" is a per-backend question — but
    this read only ``KATO_AGENT_BACKEND``, so a host configured for Claude
    could never surface a stale Codex CLI, and the upgrade button the Claude
    tab offers would have upgraded the wrong CLI for the Codex one.
    """
    explicit = str(backend or '').strip()
    if explicit:
        try:
            from agent_backend_core_lib.agent_backend_core_lib.client.agent_client_factory import (
                resolve_platform,
            )
            return resolve_platform(explicit).value
        except Exception:
            low = explicit.lower()
            if low.startswith('claude'):
                return 'claude'
            if 'codex' in low:
                return 'codex'
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
    if AgentBackend.is_a(backend, AgentBackend.CLAUDE):
        return env.get('KATO_CLAUDE_BINARY', '').strip() or 'claude'
    if AgentBackend.is_a(backend, AgentBackend.CODEX):
        return env.get('KATO_CODEX_BINARY', '').strip() or 'codex'
    return ''


def _min_version(backend: str, env: dict) -> str:
    if AgentBackend.is_a(backend, AgentBackend.CLAUDE):
        return (env.get('KATO_CLAUDE_MIN_VERSION', '') or _CLAUDE_MIN_DEFAULT).strip()
    if AgentBackend.is_a(backend, AgentBackend.CODEX):
        return (env.get('KATO_CODEX_MIN_VERSION', '') or '').strip()
    return ''


def _fetch_latest_package(package: str) -> dict:
    """What npm currently publishes as ``latest`` for ``package``.

    Returns ``{'version': str|None, 'node': str}`` — ``node`` being the
    package's declared ``engines.node`` range, which decides whether an
    ``npm install`` on this host would even succeed. Public registry, no auth,
    tiny response. Returns empty values on any failure: an offline host must
    degrade to "nothing known", never to a wrong claim or an exception.
    """
    url = _NPM_REGISTRY_URL.format(package=package)
    try:
        request = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(request, timeout=_REGISTRY_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except Exception:
        return {'version': None, 'node': ''}
    if not isinstance(payload, dict):
        return {'version': None, 'node': ''}
    version = payload.get('version')
    engines = payload.get('engines')
    node = engines.get('node') if isinstance(engines, dict) else ''
    return {
        'version': str(version).strip() if version else None,
        'node': str(node or '').strip(),
    }


def latest_published_package(backend: str, fetcher=None) -> dict:
    """Cached registry metadata for a backend's CLI package.

    ``fetcher`` is injectable for tests; the default hits the npm registry.
    """
    package = _NPM_PACKAGES.get(backend, '')
    if not package:
        return {'version': None, 'node': ''}
    now = time.monotonic()
    with _latest_lock:
        cached = _latest_cache.get(package)
        if cached and (now - cached[0]) < _LATEST_TTL_SECONDS:
            return dict(cached[1])
    meta = (fetcher or _fetch_latest_package)(package)
    if not isinstance(meta, dict):
        meta = {'version': None, 'node': ''}
    with _latest_lock:
        _latest_cache[package] = (time.monotonic(), dict(meta))
    return dict(meta)


def latest_published_version(backend: str, fetcher=None) -> str | None:
    """Cached ``latest`` version for a backend's CLI package (``None`` if unknown)."""
    return latest_published_package(backend, fetcher).get('version')


def reset_latest_version_cache() -> None:
    """Drop the published-version cache (tests, and the UI's explicit refresh)."""
    with _latest_lock:
        _latest_cache.clear()


def _download_url(backend: str, env: dict) -> str:
    if AgentBackend.is_a(backend, AgentBackend.CLAUDE):
        return (env.get('KATO_CLAUDE_DOWNLOAD_URL', '') or _CLAUDE_DOWNLOAD_DEFAULT).strip()
    if AgentBackend.is_a(backend, AgentBackend.CODEX):
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


def installed_version(
    env: dict | None = None, runner=None, backend: str = '',
) -> str | None:
    """Just the installed CLI's ``'x.y.z'`` (or ``None``) — no registry lookup.

    The before/after probe around an upgrade needs the local version only;
    going through ``agent_version_info`` would also hit the npm registry on
    both sides of a command that already knows it changed things.
    """
    env = _config_env() if env is None else env
    backend = _resolve_backend(env, backend)
    if backend == 'openhands':
        return None
    found, raw = _probe(_binary_for(backend, env), runner=runner)
    if not found:
        return None
    version = parse_version(raw)
    return '.'.join(str(n) for n in version) if version else None


def agent_version_info(
    env: dict | None = None, runner=None, latest=None, backend: str = '',
) -> dict:
    """Report the configured backend's CLI version + capability flags.

    Keys: ``backend``, ``binary``, ``found``, ``version`` (``'x.y.z'``/None),
    ``version_raw``, ``recommended_min``, ``up_to_date`` (meets the recommended
    floor), ``latest_version`` (published right now, or None), ``update_available``
    (installed is behind what's published), ``supports_workflows`` (claude + new
    enough), ``detail``. ``latest`` is an injectable lookup for tests. Never raises.
    """
    env = _config_env() if env is None else env
    backend = _resolve_backend(env, backend)
    info = {
        'backend': backend, 'binary': '', 'found': True, 'version': None,
        'version_raw': '', 'recommended_min': '', 'up_to_date': True,
        'latest_version': None, 'update_available': False,
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
    _apply_published_version(info, backend, version, latest)
    _apply_upgrade_flags(info, env)
    return info


def _apply_published_version(info: dict, backend: str, version, latest) -> None:
    """Compare the installed version against what npm publishes RIGHT NOW.

    Independent of the recommended floor: an install can clear the floor and
    still be many releases behind. Unknown latest (offline, registry down,
    unparseable local version) leaves ``update_available`` False — we never
    nag on a guess.
    """
    latest_raw = latest(backend) if latest else latest_published_version(backend)
    if not latest_raw:
        return
    info['latest_version'] = latest_raw
    latest_tuple = parse_version(latest_raw)
    if version and latest_tuple:
        info['update_available'] = version < latest_tuple


def _is_truthy(value) -> bool:
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def _is_falsy(value) -> bool:
    return str(value or '').strip().lower() in ('0', 'false', 'no', 'off', 'disabled')


def _is_npm_managed(binary: str) -> bool:
    """Whether ``binary`` on PATH resolves INTO an npm global install.

    npm shims live at ``<prefix>/bin/<name>`` and symlink into
    ``<prefix>/lib/node_modules/<pkg>/…``, so the resolved real path names
    ``node_modules``. Cheap (no subprocess) and decisive: running
    ``npm install -g`` against a CLI installed by the native installer or a
    package manager would leave TWO copies and a PATH coin-flip.
    """
    path = shutil.which(binary)
    if not path:
        return False
    try:
        return 'node_modules' in os.path.realpath(path).split(os.sep)
    except OSError:
        return False


def _host_node_major(runner=None) -> int | None:
    """The running Node's major version (``None`` if node isn't on PATH)."""
    node = shutil.which('node')
    if not node:
        return None
    try:
        raw = (runner or _default_runner)(node)
    except Exception:
        return None
    match = re.search(r'v?(\d+)\.', str(raw or ''))
    return int(match.group(1)) if match else None


def _required_node_major(engines: str) -> int | None:
    """Minimum Node major from a package's ``engines.node``, if unambiguous.

    Deliberately narrow: only a plain ``>=X`` (the shape both CLIs actually
    publish) is interpreted. Any compound range (``^20 || ^22``) returns
    ``None`` so we DON'T block — a wrong "your Node is too old" would hide a
    working upgrade, which is worse than letting npm speak for itself.
    """
    text = str(engines or '').strip()
    if not text or '||' in text:
        return None
    match = re.fullmatch(r'>=\s*(\d+)(?:\.\d+)*', text)
    return int(match.group(1)) if match else None


def _node_engine_block_reason(backend: str, node_runner=None) -> str:
    """Why an npm upgrade would fail on this host's Node, or ``''``.

    npm refuses the install outright (``EBADENGINE``) when the published
    package needs a newer Node than the host runs, so offering the button
    would guarantee a failure. Fails OPEN — anything unknown returns ``''``.
    """
    meta = latest_published_package(backend)
    required = _required_node_major(meta.get('node', ''))
    if required is None:
        return ''
    host = _host_node_major(runner=node_runner)
    if host is None or host >= required:
        return ''
    version = meta.get('version') or 'the latest release'
    return (
        f'{_NPM_PACKAGES.get(backend, "the CLI")} {version} needs Node '
        f'>={required} but this host runs Node {host} — update Node (or use '
        'the native installer from the download page), then upgrade'
    )


def _npm_plan(plan: dict, npm: str, package: str, backend: str) -> dict:
    """Fill ``plan`` with the npm upgrade, unless this host's Node is too old."""
    blocked = _node_engine_block_reason(backend)
    if blocked:
        plan['reason'] = blocked
        return plan
    plan.update(
        allowed=True, manager='npm',
        argv=[npm, 'install', '-g', f'{package}@latest',
              '--no-fund', '--no-audit', '--loglevel=http'],
        command=f'npm install -g {package}@latest',
    )
    return plan


def upgrade_plan(env: dict | None = None, backend: str = '') -> dict:
    """How an in-app upgrade would be performed on THIS host.

    Returns ``{allowed, reason, manager, argv, command}``. Two managers:

    * ``npm``  — the CLI came from an npm global install, so upgrade the
      package. Used whenever the binary resolves into ``node_modules`` (and,
      for back-compat, whenever we can't tell but npm is present).
    * ``cli``  — a native/self-managed install: defer to the CLI's OWN updater
      (``claude update``), which knows how it was installed. This used to be a
      dead end that just reported "npm not found".

    An npm plan is refused when the published package needs a newer Node than
    this host runs: npm would abort with ``EBADENGINE``, so the button would be
    a guaranteed failure. The reason names the fix instead.

    ``argv`` is assembled from a FIXED template plus paths resolved off PATH —
    never from user input.
    """
    env = _config_env() if env is None else env
    plan = {'allowed': False, 'reason': '', 'manager': '', 'argv': [], 'command': ''}
    allowed, reason = upgrade_allowed(env, backend)
    if not allowed:
        plan['reason'] = reason
        return plan

    backend = _resolve_backend(env, backend)
    binary = _binary_for(backend, env)
    package = _NPM_PACKAGES.get(backend, '')
    npm = shutil.which('npm')
    npm_usable = bool(package and npm)
    prefers_npm = npm_usable and (_is_npm_managed(binary) or not shutil.which(binary))

    if prefers_npm:
        return _npm_plan(plan, npm, package, backend)
    if AgentBackend.is_a(backend, AgentBackend.CLAUDE):
        claude = shutil.which(binary)
        if claude:
            plan.update(allowed=True, manager='cli', argv=[claude, 'update'],
                        command=f'{binary} update')
            return plan
    if npm_usable:
        return _npm_plan(plan, npm, package, backend)
    plan['reason'] = (
        f'no supported upgrade path for {binary} on this host '
        '(npm not on PATH and the CLI has no self-updater) — use the download page'
    )
    return plan


def upgrade_command_str(env: dict | None = None, backend: str = '') -> str:
    """The exact command an in-app upgrade runs (shown to the operator for
    approval). Fixed template — never built from user input."""
    return upgrade_plan(env, backend)['command']


def upgrade_allowed(env: dict | None = None, backend: str = '') -> tuple[bool, str]:
    """``(allowed, reason)`` for an in-app CLI upgrade. Available by default for
    the claude and codex CLIs on the host (both ship as npm packages); an
    operator can hard-disable it with ``KATO_ALLOW_CLI_UPGRADE=false``. Not
    offered in Docker (the CLI lives in the image — rebuild it with
    ``kato sandbox build``). The per-use confirm in the UI (showing the exact
    command) is the approval gate."""
    env = _config_env() if env is None else env
    if _is_falsy(env.get('KATO_ALLOW_CLI_UPGRADE')):
        return False, 'in-app upgrade is disabled (KATO_ALLOW_CLI_UPGRADE=false)'
    backend = _resolve_backend(env, backend)
    if backend not in _NPM_PACKAGES:
        return False, (
            f'in-app upgrade supports only the claude and codex CLIs (backend '
            f'is {backend}) — use the download page'
        )
    if _is_truthy(env.get('KATO_CLAUDE_DOCKER')):
        return False, (
            'Docker sandbox mode — the CLI is in the image; rebuild with '
            '`kato sandbox build`'
        )
    return True, ''


def _apply_upgrade_flags(info: dict, env: dict) -> None:
    # "Needs an upgrade" is EITHER a newer published release or an install
    # below the recommended floor. Gating solely on the floor is what hid the
    # button while the host sat dozens of releases behind.
    outdated = bool(info['update_available'] or not info['up_to_date'])
    plan = upgrade_plan(env, info.get('backend', ''))
    info['can_upgrade'] = bool(plan['allowed'] and outdated)
    info['upgrade_command'] = plan['command'] if info['can_upgrade'] else ''
    # Why one-click upgrade isn't offered — only when there IS an update we
    # can't apply in-app (Docker image, unsupported backend, no upgrade path,
    # or hard-disabled), so the banner can tell the operator what to do
    # instead of going silent.
    info['upgrade_blocked_reason'] = (
        plan['reason'] if (outdated and not plan['allowed']) else ''
    )


def _default_upgrade_runner(cmd: list) -> tuple[int, str]:
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding='utf-8',
        errors='replace', check=False, timeout=_UPGRADE_TIMEOUT_SECONDS,
    )
    return result.returncode, ((result.stdout or '') + (result.stderr or '')).strip()


UPGRADE_SUCCESS_MESSAGE = (
    'upgraded — new tasks & chats use it immediately; an in-flight chat '
    'applies it on its next start (no kato restart needed)'
)


def upgrade_agent_cli(env: dict | None = None, runner=None, backend: str = '') -> dict:
    """Run the gated, FIXED upgrade command for the configured CLI on the host.

    Caller is responsible for the operator's per-use approval (the UI confirm);
    this enforces the server-side gate (``upgrade_plan``) + runs ONLY the
    planned command. Returns ``{ok, message, output, version_before,
    version_after}``. Never raises. For the progress-reporting variant the UI
    uses, see ``agent_cli_upgrade_job``.
    """
    env = _config_env() if env is None else env
    plan = upgrade_plan(env, backend)
    if not plan['allowed']:
        return {'ok': False, 'message': plan['reason'], 'output': '',
                'version_before': None, 'version_after': None}
    before = installed_version(env, backend=backend)
    run = runner or _default_upgrade_runner
    try:
        code, output = run(plan['argv'])
    except Exception as exc:
        return {'ok': False, 'message': f'upgrade failed to run: {exc}',
                'output': '', 'version_before': before, 'version_after': None}
    reset_latest_version_cache()
    after = installed_version(env, backend=backend)
    return {
        'ok': code == 0,
        'message': (UPGRADE_SUCCESS_MESSAGE if code == 0
                    else f"{plan['manager']} exited with code {code}"),
        'output': output,
        'version_before': before,
        'version_after': after,
    }
