"""Build, gate, and wrap the Claude sandbox container.

Three responsibilities:

1. **Preflight** (``check_docker_or_exit``) — called from kato startup
   when ``KATO_CLAUDE_BYPASS_PERMISSIONS=true`` is set. Refuses to
   start the agent if Docker isn't installed and running.

2. **Build** (``ensure_image``) — called lazily on the first
   sandboxed spawn. Builds ``kato/claude-sandbox:latest`` from the
   Dockerfile next to this module if it isn't already present in the
   local image cache. Subsequent spawns are zero-overhead.

3. **Wrap** (``wrap_command``) — turns the existing
   ``[claude, -p, ...]`` argv into a ``[docker, run, ..., claude,
   -p, ...]`` argv. The stdin/stdout NDJSON contract is unchanged so
   the streaming-session reader threads don't care whether they're
   talking to a host process or a container.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

SANDBOX_IMAGE_TAG = 'kato/claude-sandbox:latest'
_SANDBOX_DIR = Path(__file__).resolve().parent
_AUTH_VOLUME_NAME = 'kato-claude-config'
_WORKSPACE_MOUNT = '/workspace'
_CLAUDE_HOME = '/home/claude'
# Custom Docker bridge network with inter-container communication
# disabled. Two parallel sandbox containers can each reach
# api.anthropic.com but cannot reach each other, so a malicious turn
# in one task can't pivot through a sibling sandbox.
_SANDBOX_NETWORK_NAME = 'kato-sandbox-net'

# Audit log: one JSON line per sandboxed spawn so the operator has a
# durable record of every container kato launched, surviving kato
# restarts. Lives at ``~/.kato/sandbox-audit.log`` by default; the
# directory is created on first write.
_DEFAULT_AUDIT_LOG_PATH = Path.home() / '.kato' / 'sandbox-audit.log'

# Operator overrides for the two strict-by-default checks. Both
# default to "off" — kato refuses to launch unless the operator
# explicitly opts in. The escape hatches exist for:
#   - macOS / Docker Desktop where gVisor isn't installable,
#   - one-off tasks where committed-secret-shaped files are
#     intentional repo fixtures (e.g. a security-research project).
ALLOW_NO_GVISOR_ENV_KEY = 'KATO_SANDBOX_ALLOW_NO_GVISOR'
ALLOW_WORKSPACE_SECRETS_ENV_KEY = 'KATO_SANDBOX_ALLOW_WORKSPACE_SECRETS'
_TRUE_VALUES = frozenset({'1', 'true', 'yes', 'on'})


def _env_flag_true(env: dict | None, key: str) -> bool:
    source = env if env is not None else os.environ
    return str(source.get(key, '')).strip().lower() in _TRUE_VALUES

# Resource ceilings — high enough for normal Claude work (lots of
# small file edits, a few hundred MB of model context), low enough
# that a runaway turn can't take down the host.
_MEMORY_LIMIT = '2g'
_PIDS_LIMIT = '256'
_CPUS_LIMIT = '2'

# Env vars on the host that are passed through into the container.
# ``ANTHROPIC_API_KEY`` lets users skip the interactive ``claude
# /login`` flow. The two telemetry / auto-update flags are baked
# into the image already; we re-pass them for explicit override.
_PASS_THROUGH_ENV = (
    'ANTHROPIC_API_KEY',
    'CLAUDE_CODE_OAUTH_TOKEN',
)

# Label the Dockerfile stamps so we can verify the cached image was
# actually built by us, not a same-named image from another source.
_IMAGE_IDENTITY_LABEL = 'org.kato.sandbox'
_IMAGE_IDENTITY_VALUE = 'true'

# Refuse to bind-mount any of these — handing Claude the operator's
# whole machine through a misconfigured workspace path would defeat
# the entire sandbox. The list is intentionally aggressive: better to
# refuse a legitimate-but-weird workspace path than silently expose
# sensitive directories.
_FORBIDDEN_MOUNT_SOURCES = frozenset({
    Path('/'),
    Path('/root'),
    Path('/home'),
    Path('/etc'),
    Path('/usr'),
    Path('/var'),
    Path('/bin'),
    Path('/sbin'),
    Path('/lib'),
    Path('/boot'),
    Path('/dev'),
    Path('/proc'),
    Path('/sys'),
    Path('/Users'),
    Path('/private'),
    Path('/Library'),
    Path('/System'),
    Path('/Applications'),
    Path('/Volumes'),
    Path.home(),
})


class SandboxError(RuntimeError):
    """Raised when the sandbox cannot be prepared or launched."""


# ----- preflight -----

def docker_available() -> bool:
    """True when ``docker`` is on PATH and the daemon answers ``info``."""
    if shutil.which('docker') is None:
        return False
    try:
        result = subprocess.run(
            ['docker', 'info', '--format', '{{.ServerVersion}}'],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def gvisor_runtime_available() -> bool:
    """True when ``runsc`` (gVisor) is configured as a Docker runtime.

    gVisor adds syscall-level isolation on top of namespaces and
    capabilities — a second kernel, in userspace, between the
    container and the host. When available we automatically use it
    via ``--runtime=runsc`` for the strongest isolation kato can offer.
    """
    try:
        result = subprocess.run(
            ['docker', 'info', '--format', '{{json .Runtimes}}'],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    try:
        runtimes = json.loads(result.stdout.strip() or '{}')
    except json.JSONDecodeError:
        return False
    return isinstance(runtimes, dict) and 'runsc' in runtimes


def docker_running_rootless() -> bool:
    """True when the Docker daemon is running in rootless mode.

    Rootless mode confines a container escape to the operator's
    user account rather than full root on the host. We don't refuse
    to start without it (it's a daemon-side configuration), but we
    surface a one-line recommendation at boot when bypass is on and
    the daemon is rooted.
    """
    try:
        result = subprocess.run(
            ['docker', 'info', '--format', '{{.SecurityOptions}}'],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return 'rootless' in result.stdout.lower()


def check_gvisor_or_exit(*, env: dict | None = None) -> None:
    """Refuse to start unless gVisor is configured, or operator overrides.

    gVisor (``runsc``) puts a userspace kernel between the container
    and the host, so most Linux-kernel CVEs cannot be used to escape
    the sandbox. With bypass mode on, that's a meaningful additional
    layer — Claude can run any command, and the container's only
    remaining isolation from the host is the kernel itself.

    Strict by default: if gVisor isn't available kato refuses to
    start. The override ``KATO_SANDBOX_ALLOW_NO_GVISOR=true`` exists
    for environments where gVisor can't be installed (most notably
    Docker Desktop on macOS / Windows, where the underlying VM is
    locked down).
    """
    if gvisor_runtime_available():
        return
    if _env_flag_true(env, ALLOW_NO_GVISOR_ENV_KEY):
        return
    bar = '=' * 78
    sys.stderr.write(
        '\n'.join((
            '',
            bar,
            'Kato cannot start: gVisor (runsc) is required for bypass mode.',
            '',
            'When KATO_CLAUDE_BYPASS_PERMISSIONS=true, kato runs Claude inside',
            'a hardened sandbox. Without gVisor, the only thing isolating the',
            'container from your host is the Linux kernel itself — a single',
            'kernel CVE could be used to escape. gVisor adds a userspace',
            'kernel between them, which is much harder to break.',
            '',
            'Pick one:',
            '  1. Install gVisor and register it as a Docker runtime:',
            '       https://gvisor.dev/docs/user_guide/install/',
            '       (then `docker info` should list "runsc" under Runtimes)',
            '  2. If you cannot install gVisor (e.g. Docker Desktop on macOS',
            '     or Windows where the underlying VM is locked down), you can',
            '     accept the residual kernel-CVE risk by setting:',
            f'       export {ALLOW_NO_GVISOR_ENV_KEY}=true',
            '     The other 8 sandbox layers (cap-drop, read-only rootfs,',
            '     egress firewall, etc.) still apply. See BYPASS_PROTECTIONS.md.',
            '  3. Or unset KATO_CLAUDE_BYPASS_PERMISSIONS to run Claude on',
            '     the host with permission prompts in the planning UI.',
            bar,
            '',
        )),
    )
    sys.stderr.flush()
    sys.exit(1)


def check_docker_or_exit() -> None:
    """Print a clear CLI message and ``sys.exit(1)`` if Docker is unavailable.

    Called from ``kato.main`` immediately after the bypass flag is
    consulted. The intent is: if the operator turned on
    ``KATO_CLAUDE_BYPASS_PERMISSIONS`` they accepted that Claude needs
    a hardened sandbox, and that sandbox needs Docker. We refuse to
    fall back to host execution silently — too easy to miss.
    """
    if docker_available():
        return
    bar = '=' * 78
    sys.stderr.write(
        '\n'.join((
            '',
            bar,
            'Kato cannot start: sandbox required but Docker is not available.',
            '',
            'You set KATO_CLAUDE_BYPASS_PERMISSIONS=true. In this mode kato runs',
            'Claude inside a hardened Docker sandbox so '
            '--permission-mode bypassPermissions',
            "can't reach beyond the per-task workspace folder. The sandbox needs",
            "Docker, and ``docker info`` doesn't currently work on this machine.",
            '',
            'Pick one:',
            '  1. Install Docker Desktop (or your distro\'s docker package) and',
            '     start it, then re-run `make compose-up`. Verify with:',
            '         docker info',
            '  2. Or unset the flag to run Claude on the host with permission',
            '     prompts in the planning UI:',
            '         unset KATO_CLAUDE_BYPASS_PERMISSIONS',
            bar,
            '',
        )),
    )
    sys.stderr.flush()
    sys.exit(1)


# ----- image build -----

def image_exists(image_tag: str = SANDBOX_IMAGE_TAG) -> bool:
    try:
        result = subprocess.run(
            ['docker', 'image', 'inspect', image_tag],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def image_built_by_kato(image_tag: str = SANDBOX_IMAGE_TAG) -> bool:
    """True when the cached image carries our identity label.

    Defends against a same-named image of unknown provenance sitting
    in the local Docker cache. ``ensure_image`` rebuilds when this
    returns False — the rebuild stamps the label as part of its
    Dockerfile, so subsequent runs see it.
    """
    try:
        result = subprocess.run(
            [
                'docker', 'image', 'inspect',
                '--format', '{{ index .Config.Labels "' + _IMAGE_IDENTITY_LABEL + '" }}',
                image_tag,
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return result.stdout.strip() == _IMAGE_IDENTITY_VALUE


def build_image(
    *,
    image_tag: str = SANDBOX_IMAGE_TAG,
    logger: logging.Logger | None = None,
) -> None:
    """Build ``image_tag`` from the Dockerfile next to this module.

    Streams docker's stdout to the logger so the operator sees the
    ``apt-get`` / ``npm install`` progress on first build (~1 minute
    on a warm npm cache, longer cold). Raises ``SandboxError`` with
    the captured output on failure so the caller can surface a
    clear "build failed" message.
    """
    if logger is not None:
        logger.info(
            'building Claude sandbox image %s — first run, may take ~1 min',
            image_tag,
        )
    cmd = ['docker', 'build', '-t', image_tag, str(_SANDBOX_DIR)]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(
            f'sandbox image build timed out after 10 minutes: {exc}',
        ) from exc
    except OSError as exc:
        raise SandboxError(
            f'failed to invoke docker build: {exc}',
        ) from exc
    if result.returncode != 0:
        raise SandboxError(
            'sandbox image build failed:\n'
            f'STDOUT:\n{result.stdout}\n'
            f'STDERR:\n{result.stderr}',
        )
    if logger is not None:
        logger.info('sandbox image %s ready', image_tag)


def ensure_image(
    *,
    image_tag: str = SANDBOX_IMAGE_TAG,
    logger: logging.Logger | None = None,
) -> None:
    """Idempotent: build the image if missing or not built by kato.

    The identity-label check forces a rebuild when a same-tagged image
    of unknown provenance is sitting in the cache (e.g. operator
    pulled something or built it from a different source). The
    rebuild restamps the label so subsequent calls short-circuit.

    Also ensures the isolated bridge network exists so parallel
    sandboxes can't reach each other.
    """
    if image_exists(image_tag) and image_built_by_kato(image_tag):
        ensure_network(logger=logger)
        return
    if image_exists(image_tag) and not image_built_by_kato(image_tag) and logger is not None:
        logger.warning(
            'sandbox image %s exists but lacks the kato identity label; '
            'rebuilding from %s to ensure the configured hardening applies',
            image_tag, _SANDBOX_DIR,
        )
    build_image(image_tag=image_tag, logger=logger)
    ensure_network(logger=logger)


def ensure_network(*, logger: logging.Logger | None = None) -> None:
    """Idempotently create the isolated sandbox bridge network.

    The custom bridge sets ``com.docker.network.bridge.enable_icc=false``
    so two parallel sandbox containers (e.g. kato spawning Claude for
    two tasks at once) cannot communicate with each other — each is
    its own island that can only reach api.anthropic.com.
    """
    try:
        result = subprocess.run(
            ['docker', 'network', 'inspect', _SANDBOX_NETWORK_NAME],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if result.returncode == 0:
        return
    create_cmd = [
        'docker', 'network', 'create',
        '--driver', 'bridge',
        '--opt', 'com.docker.network.bridge.enable_icc=false',
        '--opt', 'com.docker.network.bridge.enable_ip_masquerade=true',
        _SANDBOX_NETWORK_NAME,
    ]
    try:
        result = subprocess.run(
            create_cmd, capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if logger is not None:
            logger.warning(
                'failed to create sandbox network %s (%s); '
                'falling back to default bridge',
                _SANDBOX_NETWORK_NAME, exc,
            )
        return
    if result.returncode != 0 and logger is not None:
        logger.warning(
            'failed to create sandbox network %s: %s — falling back to default bridge',
            _SANDBOX_NETWORK_NAME, result.stderr.strip() or '(no stderr)',
        )


def _validate_workspace_path(workspace_path: str) -> str:
    """Resolve ``workspace_path`` and refuse anything that would expose host state.

    The bind mount is the only file-level seam between the sandbox and
    the host. A misconfigured workspace path (typo, env var pointing
    at ``$HOME``, an attacker-influenced config) would hand Claude the
    operator's whole machine. We reject:

    - empty / unset paths,
    - common system roots (``/``, ``/etc``, ``/usr``, …) and the
      operator's home directory (mounting ``$HOME`` would give Claude
      ``~/.ssh``, ``~/.aws``, ``~/.gnupg``, every other secret),
    - anything that doesn't actually exist on disk (typos),
    - anything that isn't a directory.
    """
    if not workspace_path or not str(workspace_path).strip():
        raise SandboxError(
            'sandbox workspace path is empty — refusing to mount '
            'unspecified path into the container',
        )
    resolved = Path(workspace_path).expanduser().resolve()
    if resolved in _FORBIDDEN_MOUNT_SOURCES:
        raise SandboxError(
            f'sandbox workspace path {resolved} is a system or home '
            'directory — refusing to bind-mount it. Check '
            'KATO_WORKSPACES_ROOT and the per-task workspace layout.',
        )
    if not resolved.exists():
        raise SandboxError(
            f'sandbox workspace path {resolved} does not exist — '
            'refusing to bind-mount a non-existent path',
        )
    if not resolved.is_dir():
        raise SandboxError(
            f'sandbox workspace path {resolved} is not a directory — '
            'refusing to bind-mount it',
        )
    return str(resolved)


# ----- spawn wrap -----

def wrap_command(
    inner_command: list[str],
    *,
    workspace_path: str,
    image_tag: str = SANDBOX_IMAGE_TAG,
    container_name: str | None = None,
) -> list[str]:
    """Wrap ``inner_command`` (the Claude CLI argv) in a ``docker run`` argv.

    The returned argv is fed straight to ``subprocess.Popen``. Inside
    the container:

    - ``--cap-drop ALL`` then a narrow ``--cap-add NET_ADMIN/NET_RAW``
      so the entrypoint can run iptables. The Claude process itself
      runs after capabilities are dropped via ``setpriv`` in the
      entrypoint, so it has no privileges of any kind.
    - ``--security-opt no-new-privileges`` blocks setuid escalation.
    - ``--read-only`` makes the container FS immutable; only the
      bind-mounted workspace and the named auth volume are writable.
    - ``--network bridge`` so the iptables policy applies (host-network
      mode would bypass it).
    - ``--memory`` / ``--pids-limit`` / ``--cpus`` keep a runaway
      turn from starving the host.
    - The workspace is bind-mounted at ``/workspace`` (the WORKDIR);
      the operator's Claude credentials live in a persistent named
      volume so they survive container lifecycle.
    """
    workspace = _validate_workspace_path(workspace_path)
    argv: list[str] = [
        'docker', 'run',
        '--rm',
        '-i',
        '--init',                              # tini reaps zombies inside container
        '--name', container_name or make_container_name(),
    ]
    # gVisor (runsc) when available — adds a userspace kernel between
    # the container and the host, neutralising most kernel-CVE escape
    # paths. Free hardening when the operator has it installed; we
    # silently use the default (runc) otherwise.
    if gvisor_runtime_available():
        argv.extend(['--runtime', 'runsc'])
    argv.extend([
        '--network', _SANDBOX_NETWORK_NAME,    # custom bridge with --icc=false
        '--ipc=none',                          # no shared memory / sysv IPC channel
        '--cap-drop', 'ALL',
        '--cap-add', 'NET_ADMIN',              # needed only by init-firewall
        '--cap-add', 'NET_RAW',                # needed only by init-firewall
        # Needed only for the ``setpriv`` step in entrypoint.sh that
        # drops root → claude (uid 1000). Without these, setresuid
        # fails with EPERM even from root. The entrypoint's
        # ``--bounding-set=-all`` wipes them before Claude exec, so
        # the running Claude process never holds them.
        '--cap-add', 'SETUID',
        '--cap-add', 'SETGID',
        '--security-opt', 'no-new-privileges',
        '--read-only',                         # rootfs immutable
        '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m',
        '--tmpfs', '/run:rw,nosuid,nodev,size=8m',
        '--tmpfs', '/var/tmp:rw,nosuid,nodev,size=16m',
        '--shm-size=64m',                      # bound /dev/shm
        '--memory', _MEMORY_LIMIT,
        '--memory-swap', _MEMORY_LIMIT,        # disable swap (= memory) so OOM is hard
        '--pids-limit', _PIDS_LIMIT,
        '--cpus', _CPUS_LIMIT,
        '--ulimit', 'nofile=1024:1024',        # bounded fd count
        '--ulimit', 'nproc=128:128',           # bounded process count
        '--ulimit', 'core=0:0',                # disable core dumps (prevents memory→disk leak on crash)
        # Disable IPv6 entirely. The egress firewall only configures
        # ip4tables; an IPv6-capable container could route traffic
        # around it. Killing the stack at the kernel level is the
        # cleanest defense.
        '--sysctl', 'net.ipv6.conf.all.disable_ipv6=1',
        '--sysctl', 'net.ipv6.conf.default.disable_ipv6=1',
        '--sysctl', 'net.ipv6.conf.lo.disable_ipv6=1',
        # Pin DNS to public resolvers (matching the firewall allowlist)
        # so a tampered /etc/resolv.conf or hijacked Docker daemon
        # resolver can't redirect lookups to an attacker-controlled
        # server.
        '--dns', '1.1.1.1',
        '--dns', '1.0.0.1',
        '--hostname', 'kato-sandbox',
        '-v', f'{workspace}:{_WORKSPACE_MOUNT}:rw',
        '-v', f'{_AUTH_VOLUME_NAME}:{_CLAUDE_HOME}/.claude',
        '-w', _WORKSPACE_MOUNT,
    ])
    for var in _PASS_THROUGH_ENV:
        if var in os.environ:
            # `-e VAR` (no value) means "pass through from the host
            # env" — keeps the secret out of the docker argv that
            # shows up in `ps`.
            argv.extend(['-e', var])
    argv.append(image_tag)
    argv.extend(inner_command)
    return argv


# ----- pre-spawn workspace secret scan -----

# File names that strongly indicate operator credentials, not normal
# committed source. Bare ``.env`` is suspicious; ``.env.example`` /
# ``.env.sample`` / ``.env.template`` are not (those are intentional
# scaffolding). Private SSH keys (``id_rsa``, ``id_ed25519``,
# ``id_ecdsa``) are always suspicious. ``credentials`` files under
# ``.aws`` / ``gcloud`` are always suspicious. Public keys (``*.pub``)
# are fine.
_SUSPICIOUS_FILE_NAMES = frozenset({
    '.env',
    '.env.local',
    '.env.production',
    '.env.prod',
    '.env.staging',
    '.netrc',
    '.git-credentials',
    'id_rsa',
    'id_ed25519',
    'id_ecdsa',
    'id_dsa',
    'credentials.json',
})

# Path-suffix matches: anything ending in these treats the whole
# subtree as suspicious. Exact-match path components (case-sensitive).
_SUSPICIOUS_PATH_SUFFIXES = (
    '.aws/credentials',
    '.aws/config',
    '.gcp/credentials.json',
    '.config/gcloud/credentials.db',
    '.config/gcloud/application_default_credentials.json',
    '.kube/config',
    '.docker/config.json',
)

# Hard cap so a workspace with thousands of files doesn't make the
# preflight noticeably slower. ``rglob`` is depth-first; once we hit
# the cap we stop scanning and warn that scan was truncated.
_SECRET_SCAN_FILE_CAP = 20_000


def scan_workspace_for_secrets(
    workspace_path: str,
    *,
    logger: logging.Logger | None = None,
) -> list[str]:
    """Walk the workspace looking for files that smell like operator secrets.

    Returns the list of relative paths that match (empty if none).
    Doesn't *block* the spawn — Claude could legitimately be working
    on a repo that ships test fixtures with these names — but logs a
    visible warning so the operator knows what the agent will be able
    to read. False-positive-prone by design; real secrets in a
    workspace are a much bigger deal than a noisy warning.
    """
    try:
        root = Path(workspace_path).resolve()
    except (OSError, RuntimeError):
        return []
    if not root.is_dir():
        return []
    findings: list[str] = []
    scanned = 0
    truncated = False
    try:
        for entry in root.rglob('*'):
            scanned += 1
            if scanned > _SECRET_SCAN_FILE_CAP:
                truncated = True
                break
            if not entry.is_file():
                continue
            if entry.name in _SUSPICIOUS_FILE_NAMES:
                findings.append(str(entry.relative_to(root)))
                continue
            relative_str = str(entry.relative_to(root))
            for suffix in _SUSPICIOUS_PATH_SUFFIXES:
                if relative_str == suffix or relative_str.endswith('/' + suffix):
                    findings.append(relative_str)
                    break
    except (OSError, PermissionError):
        # Best-effort: if we can't traverse a subtree we just log
        # what we found so far and move on.
        pass
    if findings and logger is not None:
        head = ', '.join(findings[:5])
        rest = f' (+{len(findings) - 5} more)' if len(findings) > 5 else ''
        truncated_note = ' (scan truncated at 20,000 files)' if truncated else ''
        logger.warning(
            'sandbox workspace %s contains %d file(s) that look like '
            'operator credentials Claude will be able to read: %s%s%s. '
            'If these are intentional repo fixtures, ignore. If not, '
            'remove or .gitignore them before continuing.',
            root, len(findings), head, rest, truncated_note,
        )
    return findings


def enforce_no_workspace_secrets(
    workspace_path: str,
    *,
    env: dict | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Refuse to spawn the sandbox when the workspace looks like it
    contains committed secrets.

    Reasoning: kato cloned this workspace from a remote, so anything
    here is something *the team committed and pushed*. A `.env`,
    `id_rsa`, or `.aws/credentials` in a remote-tracked repo is
    almost always an operator mistake — surfaced as a hard refusal
    so the team fixes it instead of shipping the next 1000 PRs with
    the leak still in tree.

    The override ``KATO_SANDBOX_ALLOW_WORKSPACE_SECRETS=true`` exists
    for legitimate cases (security-research repos, intentional test
    fixtures whose names happen to match) — operator's explicit call.
    """
    findings = scan_workspace_for_secrets(workspace_path, logger=logger)
    if not findings:
        return
    if _env_flag_true(env, ALLOW_WORKSPACE_SECRETS_ENV_KEY):
        if logger is not None:
            logger.warning(
                'proceeding with %d workspace secret-shaped file(s) — '
                '%s=true override is set; operator accepted',
                len(findings), ALLOW_WORKSPACE_SECRETS_ENV_KEY,
            )
        return
    head = ', '.join(findings[:10])
    rest = f' (+{len(findings) - 10} more)' if len(findings) > 10 else ''
    raise SandboxError(
        f'workspace at {workspace_path} contains {len(findings)} file(s) '
        f'that look like committed secrets — kato refuses to launch the '
        f'sandbox so the leak is fixed at source rather than ignored: '
        f'{head}{rest}. Either remove the files and add them to '
        f'.gitignore, or set {ALLOW_WORKSPACE_SECRETS_ENV_KEY}=true to '
        f'override (only do this if these are intentional repo fixtures).'
    )


# ----- audit log + container naming -----

def make_container_name(task_id: str = '') -> str:
    """Deterministic-ish container name for ``docker ps`` / audit grep.

    Embeds the task id (or ``unknown``) plus a short uuid suffix so
    parallel spawns don't collide and the operator can find their
    task's container at a glance with ``docker ps | grep UNA-1495``.
    """
    safe_task = ''.join(
        ch if ch.isalnum() or ch in '-_' else '_'
        for ch in (str(task_id or 'unknown') or 'unknown')
    )[:48]
    return f'kato-sandbox-{safe_task}-{uuid.uuid4().hex[:8]}'


def record_spawn(
    *,
    task_id: str,
    container_name: str,
    workspace_path: str,
    image_tag: str = SANDBOX_IMAGE_TAG,
    audit_log_path: Path | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Append one JSON line per sandboxed spawn to the audit log.

    Best-effort: a write failure is logged at warning level but never
    raises, so an audit-log glitch can't break the spawn path. The
    log lives at ``~/.kato/sandbox-audit.log`` by default; lines are
    newline-delimited JSON objects with timestamp, task id, container
    name, image tag + digest (when resolvable), and workspace path.
    """
    target = audit_log_path or _DEFAULT_AUDIT_LOG_PATH
    entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'event': 'spawn',
        'task_id': str(task_id or ''),
        'container_name': container_name,
        'image_tag': image_tag,
        'image_digest': _image_digest(image_tag) or '',
        'workspace_path': workspace_path,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except OSError as exc:
        if logger is not None:
            logger.warning(
                'failed to write sandbox audit log entry to %s: %s',
                target, exc,
            )


def _image_digest(image_tag: str) -> str:
    """Best-effort: return the local image digest, empty string on failure."""
    try:
        result = subprocess.run(
            [
                'docker', 'image', 'inspect',
                '--format', '{{ index .Id }}',
                image_tag,
            ],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ''
    if result.returncode != 0:
        return ''
    return result.stdout.strip()


def login_command(image_tag: str = SANDBOX_IMAGE_TAG) -> list[str]:
    """One-time interactive ``claude /login`` invocation for the sandbox.

    Run this from a normal terminal (``-it``, not piped) to seed the
    persistent auth volume with the operator's credentials. After
    this, kato-spawned sandbox containers reuse the same volume.
    Uses the same hardening as ``wrap_command`` minus the workspace
    mount (login doesn't touch task files).
    """
    return [
        'docker', 'run',
        '--rm',
        '-it',
        '--init',
        '--network', _SANDBOX_NETWORK_NAME,
        '--ipc=none',
        '--uts', 'private',
        '--cap-drop', 'ALL',
        '--cap-add', 'NET_ADMIN',
        '--cap-add', 'NET_RAW',
        '--cap-add', 'SETUID',
        '--cap-add', 'SETGID',
        '--security-opt', 'no-new-privileges',
        '--read-only',
        '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m',
        '--tmpfs', '/run:rw,nosuid,nodev,size=8m',
        '--shm-size=32m',
        '--memory', '512m',
        '--memory-swap', '512m',
        '--pids-limit', '128',
        '--ulimit', 'nofile=1024:1024',
        '--ulimit', 'nproc=64:64',
        '--sysctl', 'net.ipv6.conf.all.disable_ipv6=1',
        '--sysctl', 'net.ipv6.conf.default.disable_ipv6=1',
        '--sysctl', 'net.ipv6.conf.lo.disable_ipv6=1',
        '--dns', '1.1.1.1',
        '--dns', '1.0.0.1',
        '--hostname', 'kato-sandbox-login',
        '-v', f'{_AUTH_VOLUME_NAME}:{_CLAUDE_HOME}/.claude',
        image_tag,
        'claude', '/login',
    ]
