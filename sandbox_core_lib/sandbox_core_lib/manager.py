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
from utils_core_lib.utils_core_lib.text_utils import text_from_mapping

import hashlib
import hmac
import json
import logging
import os
import posixpath
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from utils_core_lib.utils_core_lib.file_lock import exclusive_file_lock
from sandbox_core_lib.sandbox_core_lib import watchdog as watchdog_module


SANDBOX_IMAGE_TAG = 'kato/claude-sandbox:latest'
_SANDBOX_DIR = Path(__file__).resolve().parent
_AUTH_VOLUME_NAME = 'kato-claude-config'
_WORKSPACE_MOUNT = '/workspace'
_CLAUDE_HOME = '/home/claude'
# Read-only mount path for the persistent auth volume during *spawn*
# mode. The entrypoint copies a strict allowlist of credential files
# from here into a per-task tmpfs at $CLAUDE_HOME/.claude. Login mode
# bypasses /auth-src entirely and mounts the volume RW directly at
# .claude so ``claude /login`` can write the operator's credentials.
_AUTH_SOURCE_MOUNT = '/auth-src'
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

# Ownership labels for the orphan reaper, DERIVED from the identity
# label's namespace rather than written out again (this lib is meant to
# carry no product brand of its own; deriving keeps the brand to the one
# constant above).
_LABEL_NAMESPACE = _IMAGE_IDENTITY_LABEL.rsplit('.', 1)[0]
_OWNER_PID_LABEL = f'{_LABEL_NAMESPACE}.owner-pid'
_OWNER_BOOT_LABEL = f'{_LABEL_NAMESPACE}.owner-boot'

# Seccomp profile shipped WITH this lib and pinned explicitly on every
# spawn. See the ``--security-opt seccomp=`` block in ``wrap_command``
# for why we don't rely on the daemon's own default.
_SECCOMP_PROFILE_PATH = Path(__file__).resolve().parent / 'seccomp' / 'agent.json'

# Where the per-spawn secret drop is mounted inside the container. The
# entrypoint reads an ALLOWLIST of names from here — never "export
# everything present", which would let a poisoned drop inject LD_PRELOAD.
_ENV_SRC_MOUNT = '/env-src'

# Refuse to bind-mount any of these — handing Claude the operator's
# whole machine through a misconfigured workspace path would defeat
# the entire sandbox. The list is intentionally aggressive: better to
# refuse a legitimate-but-weird workspace path than silently expose
# sensitive directories.
#
# Two flavours of refusal:
#
#   * ``_FORBIDDEN_MOUNT_SOURCES_SUBTREE`` — the path itself **and any
#     descendant** is refused. Used for system roots like ``/etc``
#     (mounting ``/etc/foo`` would expose ``/etc/passwd``-adjacent
#     state) and for sensitive subtrees of the operator's home like
#     ``~/.ssh``, ``~/.aws``, ``~/.gnupg``.
#
#   * ``_FORBIDDEN_MOUNT_SOURCES_EXACT`` — only the exact path is
#     refused, descendants are allowed. Used for ``/`` (obviously
#     can't subtree-block since everything is under it), for the
#     "home roots" ``/home`` and ``/Users`` (their immediate
#     subdirectories are user homes, which are legitimate workspace
#     parents), and for the operator's own ``$HOME`` (per-task
#     workspaces typically live somewhere under it, but the home
#     dir itself is never a valid workspace).
_FORBIDDEN_MOUNT_SOURCES_SUBTREE = frozenset({
    Path('/root'),
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
    # Docker daemon socket / state — mounting any of these would
    # let the sandboxed Claude talk to the host Docker daemon and
    # spawn an unconstrained container with /:host bind-mounted
    # (classic container escape via docker.sock). Subtree, so
    # ``/var/run/docker.sock``-adjacent paths are blocked too.
    Path('/var/run/docker.sock'),
    Path('/var/lib/docker'),
    Path('/var/lib/containerd'),
    Path('/run/docker.sock'),
    Path('/run/containerd'),
    Path('/private'),
    Path('/Library'),
    Path('/System'),
    Path('/Applications'),
    Path('/Volumes'),
    # Sensitive subtrees under the operator's $HOME. Subdirs of
    # these (e.g. ``~/.ssh/authorized_keys``) are blocked too.
    Path.home() / '.ssh',
    Path.home() / '.aws',
    Path.home() / '.gnupg',
    Path.home() / '.gcp',
    Path.home() / '.kube',
    Path.home() / '.docker',
    Path.home() / '.config' / 'gcloud',
    Path.home() / '.config' / 'kato',
    # macOS keychain / app-support secrets directories.
    #
    # Broad by intent: bypass mode runs an autonomous coding agent
    # with no per-tool prompts. Mounting any of these as a workspace
    # would expose Apple ID auth tokens (IdentityServices), iMessage
    # chat history (Messages, Group Containers), Mail, Safari
    # cookies / bookmarks / history, calendar database, contacts
    # (AddressBook), call history, recently-opened-file lists, and
    # the broad Containers / Group Containers trees used by every
    # sandboxed macOS app for its private data. Operators who keep
    # workspaces under any of these subtrees should move them.
    #
    # On non-macOS hosts (Linux / WSL2) these paths simply don't
    # exist; ``_validate_workspace_path``'s exists() check would
    # catch them anyway, so the entries are harmless on Linux.
    Path.home() / 'Library' / 'Keychains',
    Path.home() / 'Library' / 'Cookies',
    Path.home() / 'Library' / 'Mail',
    Path.home() / 'Library' / 'Messages',
    Path.home() / 'Library' / 'Safari',
    Path.home() / 'Library' / 'Calendars',
    Path.home() / 'Library' / 'IdentityServices',
    Path.home() / 'Library' / 'Group Containers',
    Path.home() / 'Library' / 'Containers',
    Path.home() / 'Library' / 'Application Support' / 'Google' / 'Chrome',
    Path.home() / 'Library' / 'Application Support' / 'Firefox',
    Path.home() / 'Library' / 'Application Support' / 'com.apple.sharedfilelist',
    Path.home() / 'Library' / 'Application Support' / 'AddressBook',
    Path.home() / 'Library' / 'Application Support' / 'Knowledge',
    Path.home() / 'Library' / 'Application Support' / 'CallHistoryDB',
})
_FORBIDDEN_MOUNT_SOURCES_EXACT = frozenset({
    Path('/'),
    Path('/home'),
    Path('/Users'),
    Path.home(),
    # ``~/.kato`` itself is refused (it holds the audit log + lock,
    # plus per-task workspace clones at ``~/.kato/workspaces/`` by
    # default). Mounting the whole dir would let Claude see the audit
    # log and any sibling task's workspace. Descendants are allowed —
    # the legitimate per-task workspace path is
    # ``~/.kato/workspaces/<task_id>/<repo>/``.
    Path.home() / '.kato',
})


# ============================================================================
# Security invariants — single source of truth, kept in sync with
# BYPASS_PROTECTIONS.md by tests/test_bypass_protections_doc_consistency.py
# ============================================================================
#
# Each constant below is the canonical declaration of a security-relevant
# property of the sandbox. The companion test asserts SET-EQUALITY against
# anchored sections in ``BYPASS_PROTECTIONS.md`` and (where mechanical
# verification is possible) against the actual ``wrap_command`` argv.
#
# To add, remove, or rename anything in any of these sets you MUST also
# update the matching anchor block in ``BYPASS_PROTECTIONS.md`` — and you
# should think very hard about whether you're changing what the threat
# model says the sandbox guarantees. The drift guard exists to make that
# decision impossible to skip silently.

# Required Docker run flags. Every entry MUST appear in ``wrap_command``
# argv (verified semantically by the drift-guard test). Form:
# ``--key=value`` for kv flags, ``--key`` for boolean flags. The test's
# matcher accepts either single-token (``--ipc=none``) or two-token
# (``--ipc none``) form in argv.
_REQUIRED_DOCKER_FLAGS = frozenset({
    '--ipc=none',
    '--cgroupns=private',
    '--cap-drop=ALL',
    '--cap-add=NET_ADMIN',
    '--cap-add=NET_RAW',
    '--cap-add=SETUID',
    '--cap-add=SETGID',
    '--cap-add=CHOWN',
    '--cap-add=SETPCAP',
    '--security-opt=no-new-privileges',
    '--security-opt=apparmor=docker-default',
    '--read-only',
})

# Forbidden Docker run flags. NONE of these may appear in ``wrap_command``
# argv (verified semantically). Each one would silently downgrade the
# threat model in a specific way; the per-flag rationale lives in the
# "Why these specific surfaces" section of BYPASS_PROTECTIONS.md.
_FORBIDDEN_DOCKER_FLAGS = frozenset({
    '--privileged',
    '--network=host',
    '--pid=host',
    '--ipc=host',
    '--uts=host',
    '--userns=host',
    '--cgroupns=host',
    '--cap-add=ALL',
    '--cap-add=SYS_ADMIN',
    '--cap-add=SYS_PTRACE',
    '--cap-add=SYS_MODULE',
    '--cap-add=SYS_BOOT',
    '--security-opt=seccomp=unconfined',
    '--security-opt=apparmor=unconfined',
    '--security-opt=systempaths=unconfined',
    '--security-opt=label=disable',
})

# Auth-volume invariants — named tags for properties that the spawn /
# login flows guarantee. Mechanical verification of each property lives
# in entrypoint.sh, wrap_command, login_command, and the Makefile. The
# drift guard ensures the named SET stays in sync with the doc.
_SECRET_DELIVERY_INVARIANTS = frozenset({
    'values-never-in-docker-config-env',
    'staged-file-mode-0600-in-dir-0700',
    'mounted-read-only',
    'entrypoint-reads-name-allowlist-only',
    'dropped-pruned-when-container-not-running',
})

_AUTH_VOLUME_INVARIANTS = frozenset({
    'spawn-source-readonly',
    'spawn-target-tmpfs',
    'spawn-credentials-allowlist',
    'spawn-bidirectional-manifest-check',
    'spawn-sha256-manifest-verify',
    'login-direct-readwrite',
    'login-only-volume-writer',
    'login-stamps-manifest',
})

# Firewall guarantees — named tags for properties of init-firewall.sh +
# the wrap_command sysctls/dns flags. Same pattern: mechanical
# enforcement is elsewhere; drift guard keeps the NAMED set in sync.
_FIREWALL_GUARANTEES = frozenset({
    'default-drop-policy',
    'allowlist-only-anthropic-tcp-443',
    'dns-only-cloudflare',
    'dns-rate-limit-60-per-minute-udp-and-tcp',
    'rfc1918-explicit-deny',
    'cloud-metadata-explicit-deny',
    'icmp-blocked',
    'ipv6-disabled',
    'fail-closed-on-anthropic-unreachable',
    'refuses-private-ip-in-allowlist',
    'isolated-non-default-network',
})

# Seccomp guarantees — named tags for properties of the pinned profile.
# The flag itself carries an absolute path (install-location dependent),
# so it cannot be a literal in ``_REQUIRED_DOCKER_FLAGS``; these named
# tags are how the doc and the code stay in sync instead. Mechanical
# enforcement lives in ``_assert_seccomp_pinned``.
_SECCOMP_GUARANTEES = frozenset({
    'explicit-profile-pinned-not-daemon-default',
    'profile-file-vendored-in-lib',
    'profile-default-action-errno',
    'never-unconfined',
    'single-seccomp-option',
})

# Threat-model classification terms used in BYPASS_PROTECTIONS.md
# tables. Adding a new term (e.g. "Bounded-with-monitoring") must
# happen in BOTH places — the drift guard catches drift either way.
_CLASSIFICATION_TERMS = frozenset({
    'Mitigated',
    'Bounded',
    'Accepted',
    'Accepted-with-mitigation',
    'Not-applicable',
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
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=5,
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
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=5,
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
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return 'rootless' in result.stdout.lower()


def check_gvisor_or_exit(*, env: dict | None = None) -> None:
    """Refuse to start without gVisor. No override.

    gVisor (``runsc``) puts a userspace kernel between the container and
    the host, so a Linux-kernel CVE cannot simply be used to escape. It
    is the only layer here that survives a kernel bug, which makes it the
    difference between "hard to escape" and "one CVE away".

    This used to be waivable with ``KATO_SANDBOX_ALLOW_NO_GVISOR=true``
    for environments where gVisor cannot be installed — notably Docker
    Desktop, whose VM is locked down. That escape hatch is GONE by
    operator decision: an isolation guarantee that any environment can
    switch off is a guarantee nobody can rely on, and the waiver was
    reached by exactly the setups that most needed the layer.

    The consequence is deliberate and worth stating plainly: on Docker
    Desktop (macOS / Windows) sandbox mode will not start at all. Run
    kato on a Linux host, or in a VM where ``runsc`` can be registered as
    a Docker runtime.

    ``env`` is still accepted so callers and tests keep one signature;
    nothing in it can re-enable a spawn without gVisor.
    """
    del env  # no flag can waive this any more
    if gvisor_runtime_available():
        return
    bar = '=' * 78
    sys.stderr.write(
        '\n'.join((
            '',
            bar,
            'Kato cannot start: gVisor (runsc) is required for sandbox mode.',
            '',
            'Sandbox mode runs the agent inside a container. Without gVisor,',
            'the only thing between that container and your host is the Linux',
            'kernel itself — one kernel CVE is an escape. gVisor puts a',
            'userspace kernel in between, which is much harder to break.',
            '',
            'There is no override for this. It was removed deliberately: the',
            'environments that used the waiver were the ones that needed the',
            'layer most, and a guarantee that can be switched off is not one.',
            '',
            'Pick one:',
            '  1. Install gVisor and register it as a Docker runtime:',
            '       https://gvisor.dev/docs/user_guide/install/',
            '       (then `docker info` should list "runsc" under Runtimes)',
            '     Docker Desktop cannot do this — use a Linux host or a VM',
            '     (Lima / Colima) where the runtime can be registered.',
            '  2. Or unset KATO_CLAUDE_DOCKER to run the agent on the host',
            '     with permission prompts in the planning UI.',
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
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=10,
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
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return result.stdout.strip() == _IMAGE_IDENTITY_VALUE


_BASE_IMAGE_ENV_KEY = 'KATO_SANDBOX_BASE_IMAGE'
_ALLOW_FLOATING_BASE_IMAGE_ENV_KEY = 'KATO_SANDBOX_ALLOW_FLOATING_BASE_IMAGE'
_CLAUDE_CLI_VERSION_ENV_KEY = 'KATO_SANDBOX_CLAUDE_CLI_VERSION'
_ALLOW_FLOATING_CLAUDE_CLI_ENV_KEY = 'KATO_SANDBOX_ALLOW_FLOATING_CLAUDE_CLI'


def _validate_base_image_pin_or_refuse(
    *,
    env: dict | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Refuse to build unless the base image is digest-pinned (strict-by-default).

    Closes the build-time supply-chain channel #17 by changing the
    default. Operators have two paths:

      1. **Recommended** — set ``KATO_SANDBOX_BASE_IMAGE`` to a
         digest-pinned reference like
         ``node:22-bookworm-slim@sha256:<digest>``. The build will use
         that exact immutable digest; a hostile registry / DNS hijack
         at build time cannot substitute the base image.
      2. **Opt-out** — set ``KATO_SANDBOX_ALLOW_FLOATING_BASE_IMAGE=true``
         to acknowledge the residual and allow the moving
         ``node:22-bookworm-slim`` tag. Operator accepts that a
         hostile network during the next build could poison the
         resulting image.

    A value set on ``KATO_SANDBOX_BASE_IMAGE`` without ``@sha256:``
    in it is also refused — half-pinning (``node:22-bookworm-slim``
    without a digest) is no protection at all and would give the
    operator a false sense of security.
    """
    source = env if env is not None else os.environ
    base = str(source.get(_BASE_IMAGE_ENV_KEY, '') or '').strip()
    allow_floating = str(
        source.get(_ALLOW_FLOATING_BASE_IMAGE_ENV_KEY, '') or ''
    ).strip().lower() in {'1', 'true', 'yes', 'on'}

    if base:
        if '@sha256:' not in base:
            raise SandboxError(
                f'{_BASE_IMAGE_ENV_KEY}={base!r} is set but does not '
                f'include a digest pin (expected '
                f'``node:22-bookworm-slim@sha256:<digest>``). A '
                f'tag-only value provides no supply-chain protection — '
                f'kato refuses to build with a half-pinned base image. '
                f'Either add the digest, or set '
                f'{_ALLOW_FLOATING_BASE_IMAGE_ENV_KEY}=true to '
                f'explicitly accept the floating-tag residual.'
            )
        if logger is not None:
            logger.info(
                'sandbox: building with digest-pinned base image %s '
                '(%s)', base, _BASE_IMAGE_ENV_KEY,
            )
        return

    if allow_floating:
        if logger is not None:
            logger.warning(
                'sandbox: building with FLOATING base image tag '
                '(%s=true). A compromised registry or hostile network '
                'at build time could substitute the base image. '
                'Recommend %s=node:22-bookworm-slim@sha256:<digest>.',
                _ALLOW_FLOATING_BASE_IMAGE_ENV_KEY,
                _BASE_IMAGE_ENV_KEY,
            )
        return

    # Strict default — refuse the build.
    raise SandboxError(
        'kato refuses to build the sandbox image without a digest-pinned '
        f'base image. The previous default (floating ``node:22-bookworm-slim`` '
        f'tag) left the build-time supply chain unbounded — a hostile '
        f'registry / DNS hijack / corporate proxy at build time could '
        f'substitute the base image and every subsequent spawn would '
        f'run poisoned binaries. Pick one:\n'
        f'  1. Recommended: export {_BASE_IMAGE_ENV_KEY}=node:22-bookworm-slim@sha256:<digest>\n'
        f'     (find the current digest with: docker manifest inspect node:22-bookworm-slim | jq -r .config.digest)\n'
        f'  2. Opt-out: export {_ALLOW_FLOATING_BASE_IMAGE_ENV_KEY}=true\n'
        f'     (operator accepts the residual; build proceeds with the floating tag)\n'
        f'See BYPASS_PROTECTIONS.md "Build-time supply chain" for detail.'
    )


def _validate_claude_cli_version_pin_or_refuse(
    *,
    env: dict | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Refuse build unless the Claude CLI version is pinned (strict-by-default).

    Closes the npm-side slice of build-time supply chain (residual
    #17) by changing the default. Without a pin, the Dockerfile
    runs ``npm install -g @anthropic-ai/claude-code`` which resolves
    ``latest`` against the npm registry — a malicious tag pushed
    between operator builds would land in the resulting image.

    Operator paths:

      1. **Recommended** — set ``KATO_SANDBOX_CLAUDE_CLI_VERSION``
         to a specific version like ``2.1.5``. The build pins
         ``@anthropic-ai/claude-code@<that version>`` instead of
         ``latest``.
      2. **Opt-out** — set ``KATO_SANDBOX_ALLOW_FLOATING_CLAUDE_CLI=true``
         to acknowledge the residual and allow ``latest``.

    Parallel to ``_validate_base_image_pin_or_refuse``: same shape,
    same opt-out pattern, same operator-friendly error message
    naming both fix paths.
    """
    source = env if env is not None else os.environ
    pinned = str(source.get(_CLAUDE_CLI_VERSION_ENV_KEY, '') or '').strip()
    allow_floating = str(
        source.get(_ALLOW_FLOATING_CLAUDE_CLI_ENV_KEY, '') or ''
    ).strip().lower() in {'1', 'true', 'yes', 'on'}

    if pinned:
        if logger is not None:
            logger.info(
                'sandbox: building with pinned Claude CLI version %s (%s)',
                pinned, _CLAUDE_CLI_VERSION_ENV_KEY,
            )
        return

    if allow_floating:
        if logger is not None:
            logger.warning(
                'sandbox: building with FLOATING Claude CLI version '
                '(%s=true). A malicious npm release pushed between '
                'operator builds could land in the resulting image. '
                'Recommend %s=<specific-version>.',
                _ALLOW_FLOATING_CLAUDE_CLI_ENV_KEY,
                _CLAUDE_CLI_VERSION_ENV_KEY,
            )
        return

    raise SandboxError(
        'kato refuses to build the sandbox image without a pinned '
        f'Claude CLI version. The previous default (``npm install -g '
        f'@anthropic-ai/claude-code@latest``) left the npm-side of the '
        f'build-time supply chain unbounded. Pick one:\n'
        f'  1. Recommended: export {_CLAUDE_CLI_VERSION_ENV_KEY}=<version>\n'
        f'     (e.g. 2.1.5; find current with: npm view @anthropic-ai/claude-code version)\n'
        f'  2. Opt-out: export {_ALLOW_FLOATING_CLAUDE_CLI_ENV_KEY}=true\n'
        f'     (operator accepts the residual; build proceeds with @latest)\n'
        f'See BYPASS_PROTECTIONS.md "Build-time supply chain" for detail.'
    )


def build_image(
    *,
    image_tag: str = SANDBOX_IMAGE_TAG,
    env: dict | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Build ``image_tag`` from the Dockerfile next to this module.

    Streams docker's stdout to the logger so the operator sees the
    ``apt-get`` / ``npm install`` progress on first build (~1 minute
    on a warm npm cache, longer cold). Raises ``SandboxError`` with
    the captured output on failure so the caller can surface a
    clear "build failed" message.

    Refuses the build (raises ``SandboxError``) unless BOTH supply-chain
    pins are satisfied (or explicitly opted out via the matching
    ``ALLOW_FLOATING_*`` env vars):

      * ``KATO_SANDBOX_BASE_IMAGE`` digest-pinned —
        see ``_validate_base_image_pin_or_refuse``.
      * ``KATO_SANDBOX_CLAUDE_CLI_VERSION`` pinned —
        see ``_validate_claude_cli_version_pin_or_refuse``.

    Both validators run before any docker invocation so a refusal
    fails fast without touching the registry.
    """
    _validate_base_image_pin_or_refuse(env=env, logger=logger)
    _validate_claude_cli_version_pin_or_refuse(env=env, logger=logger)
    if logger is not None:
        logger.info(
            'building Claude sandbox image %s — first run, may take ~1 min',
            image_tag,
        )
    cmd = ['docker', 'build', '-t', image_tag]
    # Read pin overrides from the SAME env source the validators used.
    # Previously this read ``os.environ`` directly while validators
    # honored the ``env`` parameter — a CI/test caller that passes a
    # pinned env dict could pass validation and then silently fall
    # back to floating tags during the actual build (supply-chain
    # pin bypass).
    env_source = env if env is not None else os.environ
    # Operator-side supply-chain pin: if KATO_SANDBOX_BASE_IMAGE is set
    # (typically to ``node:22-bookworm-slim@sha256:<digest>``), pass it
    # as the BASE_IMAGE build-arg so the Dockerfile pulls that exact
    # immutable digest instead of the mutable ``node:22-bookworm-slim``
    # tag. Recommended for any deployment that cares about base-image
    # tampering or reproducibility.
    base_override = text_from_mapping(env_source, 'KATO_SANDBOX_BASE_IMAGE')
    if base_override:
        cmd.extend(['--build-arg', f'BASE_IMAGE={base_override}'])
        if logger is not None:
            logger.info(
                'sandbox: pinning base image to %s (KATO_SANDBOX_BASE_IMAGE)',
                base_override,
            )
    # Operator-side npm-side supply-chain pin. If
    # KATO_SANDBOX_CLAUDE_CLI_VERSION is set (e.g. ``2.1.5``), pass
    # it as the CLAUDE_CLI_VERSION build-arg so the Dockerfile installs
    # ``@anthropic-ai/claude-code@<that version>`` instead of ``latest``.
    # Closes the build-time channel where a malicious ``latest`` could
    # be pushed to npm between operator builds. Default ``latest``
    # preserves existing behavior — operators opt into pinning when
    # their threat model requires it.
    cli_override = text_from_mapping(env_source, 'KATO_SANDBOX_CLAUDE_CLI_VERSION')
    if cli_override:
        cmd.extend(['--build-arg', f'CLAUDE_CLI_VERSION={cli_override}'])
        if logger is not None:
            logger.info(
                'sandbox: pinning Claude CLI version to %s '
                '(KATO_SANDBOX_CLAUDE_CLI_VERSION)',
                cli_override,
            )
    cmd.append(str(_SANDBOX_DIR))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=600,
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

    Fail-closed: if the isolated network can neither be inspected nor
    created, raise ``SandboxError`` rather than silently falling back
    to the default ``docker0`` bridge (which has ``enable_icc=true``,
    breaking the inter-container isolation guarantee).
    """
    try:
        result = subprocess.run(
            ['docker', 'network', 'inspect', _SANDBOX_NETWORK_NAME],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError(
            f'cannot inspect docker networks ({exc}) — refusing to '
            'launch sandbox without confirmed network isolation',
        ) from exc
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
            create_cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError(
            f'failed to create isolated sandbox network '
            f'{_SANDBOX_NETWORK_NAME} ({exc}) — refusing to launch '
            'sandbox without inter-container isolation',
        ) from exc
    if result.returncode != 0:
        stderr = result.stderr.strip() or '(no stderr)'
        if logger is not None:
            logger.error(
                'failed to create sandbox network %s: %s',
                _SANDBOX_NETWORK_NAME, stderr,
            )
        raise SandboxError(
            f'failed to create isolated sandbox network '
            f'{_SANDBOX_NETWORK_NAME}: {stderr} — refusing to launch '
            'sandbox without inter-container isolation',
        )


def _is_relative_to(child: Path, parent: Path) -> bool:
    """``Path.is_relative_to`` shim for Python <3.9 plus OSError-safe.

    Returns True when ``child`` equals ``parent`` or is nested under
    it. Falls back to a string-prefix check when path comparison
    raises (extremely rare — happens on Windows reserved names).
    """
    try:
        return child == parent or child.is_relative_to(parent)
    except (AttributeError, ValueError, OSError):
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False


def _forbidden_match(resolved: Path) -> Path | None:
    """Return the forbidden ancestor of ``resolved``, or None.

    ``_FORBIDDEN_MOUNT_SOURCES_SUBTREE`` matches the path itself and
    any descendant. ``_FORBIDDEN_MOUNT_SOURCES_EXACT`` matches only
    the exact path; descendants are allowed (this is how per-task
    workspaces under ``$HOME`` are permitted).
    """
    for forbidden in _FORBIDDEN_MOUNT_SOURCES_SUBTREE:
        if _is_relative_to(resolved, forbidden):
            return forbidden
    if resolved in _FORBIDDEN_MOUNT_SOURCES_EXACT:
        return resolved
    return None


def _validate_workspace_path(workspace_path: str) -> str:
    """Resolve ``workspace_path`` and refuse anything that would expose host state.

    The bind mount is the only file-level seam between the sandbox and
    the host. A misconfigured workspace path (typo, env var pointing
    at ``$HOME``, an attacker-influenced config) would hand Claude the
    operator's whole machine. We reject:

    - empty / unset paths,
    - common system roots and any descendants (``/etc/foo`` is just as
      bad as ``/etc``),
    - the operator's home directory itself, and any descendant of the
      sensitive subtrees under it (``~/.ssh``, ``~/.aws``,
      ``~/.gnupg``, ``~/.kube``, ``~/.docker``, ``~/.kato``, macOS
      keychain dirs, browser profile dirs),
    - anything that doesn't actually exist on disk (typos),
    - anything that isn't a directory.
    """
    if not workspace_path or not str(workspace_path).strip():
        raise SandboxError(
            'sandbox workspace path is empty — refusing to mount '
            'unspecified path into the container',
        )
    expanded = Path(workspace_path).expanduser()
    unresolved = expanded if expanded.is_absolute() else Path.cwd() / expanded
    resolved = expanded.resolve()
    match = _forbidden_match(unresolved) or _forbidden_match(resolved)
    if match is not None:
        if match == resolved:
            raise SandboxError(
                f'sandbox workspace path {resolved} is a system or home '
                'directory — refusing to bind-mount it. Check '
                'KATO_WORKSPACES_ROOT and the per-task workspace layout.',
            )
        raise SandboxError(
            f'sandbox workspace path {resolved} is under sensitive '
            f'directory {match} — refusing to bind-mount it (would '
            'expose secrets / system state to Claude). Move the '
            'workspace outside this subtree.',
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
    # Defense-in-depth: scan the top of the workspace for a Docker
    # socket. If someone has a docker-in-docker / podman-style
    # ``docker.sock`` symlink inside the workspace, mounting it lets
    # Claude pivot to the host Docker daemon and spawn an
    # unconstrained container — full host compromise. Top-level only
    # so the scan stays cheap on huge repos.
    try:
        for entry in resolved.iterdir():
            if entry.name in ('docker.sock', 'containerd.sock'):
                raise SandboxError(
                    f'sandbox workspace {resolved} contains a Docker/'
                    f'containerd socket ({entry.name}) — refusing to '
                    'mount, this would let the sandbox talk to the '
                    'host Docker daemon and escape',
                )
    except (OSError, PermissionError):
        # Best-effort: if the workspace is unreadable, we'll fail
        # later for a different reason. Don't block on transient FS.
        pass
    return str(resolved)


# ----- spawn wrap -----

def _container_workdir(workdir_subpath: str) -> str:
    """``/workspace``, or a subdirectory of it for ``workdir_subpath``.

    Refuses anything that isn't a plain relative descendant — an absolute
    path, or one climbing out with ``..``, would put the WORKDIR outside the
    bind mount and quietly undo the boundary the mount exists to create.
    Falls back to the mount root, which is always inside it.
    """
    raw = str(workdir_subpath or '').strip()
    # Absoluteness is tested BEFORE stripping separators: ``/etc`` would
    # otherwise become the relative ``etc`` and resolve to /workspace/etc —
    # inside the mount, but not what the caller asked for, which means a
    # caller bug would be silently rewritten instead of ignored.
    if not raw or os.path.isabs(raw) or posixpath.isabs(raw):
        return _WORKSPACE_MOUNT
    candidate = raw.strip('/')
    if not candidate:
        return _WORKSPACE_MOUNT
    normalized = posixpath.normpath(candidate)
    if normalized in ('.', '') or normalized.startswith('..'):
        return _WORKSPACE_MOUNT
    return posixpath.join(_WORKSPACE_MOUNT, normalized)


_GIT_DIR_SCAN_MAX_DEPTH = 3


def _git_dir_readonly_mounts(workspace: str) -> list[str]:
    """``-v <clone>/.git:<container path>:ro`` for every clone in the workspace.

    THE reason this exists: a workspace clone's ``.git`` is agent-writable
    and the HOST later runs git against that same clone. Git config is a
    command-execution surface — ``core.fsmonitor`` alone gave a working
    sandbox-to-host RCE, reproduced against this codebase — and the
    dangerous keys cannot all be neutralised with flags, because content
    filters take attacker-chosen names. Overriding keys treats symptoms;
    making the file unwritable removes the input.

    The working tree stays read-write. Only ``.git`` is frozen, so the
    agent can still read history (``git log``, ``git diff``) and edit any
    source file — it just cannot rewrite the repository's configuration,
    hooks, or refs. The host application's own commits and pushes run
    outside the container, where the directory is writable as usual, so
    the normal task flow is unaffected.

    Depth-limited: clones live at ``<workspace>/<repo>/.git`` (and one
    level deeper for nested layouts). An unbounded walk of an
    agent-controlled tree is its own denial-of-service.
    """
    root = Path(workspace)
    mounts: list[str] = []
    seen: set[str] = set()
    for depth in range(1, _GIT_DIR_SCAN_MAX_DEPTH + 1):
        for git_dir in root.glob('/'.join(['*'] * (depth - 1) + ['.git'])):
            try:
                if not git_dir.is_dir():
                    continue                     # worktree/submodule ``.git`` FILE
                relative = git_dir.relative_to(root).as_posix()
            except (OSError, ValueError):
                continue
            if relative in seen:
                continue
            seen.add(relative)
            mounts.extend([
                '-v', f'{git_dir}:{_WORKSPACE_MOUNT}/{relative}:ro',
            ])
    return mounts


def wrap_command(
    inner_command: list[str],
    *,
    workspace_path: str,
    workdir_subpath: str = '',
    image_tag: str = SANDBOX_IMAGE_TAG,
    container_name: str | None = None,
    task_id: str | None = None,
    task_network: str = '',
    proxy_ip: str = '',
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
      bind-mounted workspace and the per-task tmpfs at
      ``/home/claude/.claude`` are writable.
    - ``--network bridge`` so the iptables policy applies (host-network
      mode would bypass it).
    - ``--memory`` / ``--pids-limit`` / ``--cpus`` keep a runaway
      turn from starving the host.
    - The workspace is bind-mounted at ``/workspace`` (the WORKDIR).
    - The operator's Claude credentials live in a persistent named
      volume mounted **read-only** at ``/auth-src``. The entrypoint
      copies a strict allowlist of credential files (``.credentials.json``)
      out of /auth-src into the **per-task tmpfs** at
      ``/home/claude/.claude``. This means a poisoned ``settings.json``,
      hook script, custom slash command, MCP config, or subagent
      definition written by a previous task is **never** carried into
      this task — we copy creds only, and the tmpfs is destroyed on
      container exit so this task can persist nothing either.
    """
    workspace = _validate_workspace_path(workspace_path)
    # Resolve the container name ONCE. It is used twice — for ``--name``
    # and to key the out-of-band secret drop — and ``make_container_name``
    # embeds a random suffix, so calling it twice produced a drop whose
    # directory name did not match any running container. The prune sweep
    # keys on exactly that name, so the mismatch would have let a later
    # spawn delete a LIVE container's credentials.
    resolved_container_name = container_name or make_container_name(task_id or '')
    argv: list[str] = [
        'docker', 'run',
        '--rm',
        '-i',
        '--init',                              # tini reaps zombies inside container
        '--name', resolved_container_name,
    ]
    # Forensic labels — surface in ``docker ps --format '{{.Labels}}'``
    # and ``docker inspect`` so an investigator can correlate a running
    # or just-exited container back to the task it served, the
    # workspace it had access to, and the auth volume it pulled creds
    # from. These are not security boundaries; they are evidence.
    argv.extend([
        '--label', 'org.kato.sandbox=true',
        '--label', f'org.kato.task-id={(task_id or "unknown")[:64]}',
        '--label', f'org.kato.workspace={workspace[:200]}',
        '--label', f'org.kato.auth-volume={_AUTH_VOLUME_NAME}',
        # Ownership stamps — unlike the four above, these ARE load-bearing:
        # ``reap_orphan_sandbox_containers`` uses them to tell a container
        # whose controlling process died (reap it) from one a live process
        # is still driving (leave it alone). ``--rm`` only fires when the
        # container's own process exits, so a hard crash of the controller
        # otherwise leaves a container running with the workspace mounted.
        '--label', f'{_OWNER_PID_LABEL}={os.getpid()}',
        '--label', f'{_OWNER_BOOT_LABEL}={host_boot_identity()}',
    ])
    # gVisor (runsc) when available — adds a userspace kernel between
    # the container and the host, neutralising most kernel-CVE escape
    # paths. Free hardening when the operator has it installed; we
    # silently use the default (runc) otherwise.
    if gvisor_runtime_available():
        argv.extend(['--runtime', 'runsc'])
    # Per-task egress: the sandbox joins ONLY its private, ``--internal``
    # 2-member network and reaches the outside solely through its own
    # SNI-pinning proxy. The network and proxy are created by the SPAWN
    # path (``wrap_spawn_for_docker``) and passed in — building them here
    # would make argv construction spawn Docker infrastructure, which is
    # both a surprising side effect and why unit tests started creating a
    # network apiece until Docker ran out of address pools.
    argv.extend([
        '--network', task_network or _SANDBOX_NETWORK_NAME,
        '--ipc=none',                          # no shared memory / sysv IPC channel
        '--cgroupns=private',                  # private cgroup namespace (host cgroup tree is invisible)
        # NOTE: there is deliberately no ``--pid`` flag. Docker accepts
        # only ``host`` or ``container:<id>``; a bare ``--pid=container``
        # (or podman's ``--pid=private``) is rejected by the daemon with
        # "invalid PID mode" and kills the spawn. Omitting the flag IS
        # the private-PID-namespace default, and ``--pid=host`` stays in
        # _FORBIDDEN_DOCKER_FLAGS so the guarantee is still enforced.
        # ``--uts`` is omitted for the same reason as ``--pid``: Docker
        # accepts only ``--uts=host``, so the default already IS a private
        # UTS namespace and the host variant stays forbidden.
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
        # Needed only by the entrypoint's ``chown`` of the per-task
        # config tmpfs to the claude user. Without it the chown fails
        # and the agent gets a config dir it can neither read nor
        # write. Wiped by the same ``--bounding-set=-all`` as the rest.
        '--cap-add', 'CHOWN',
        # Needed by ``setpriv --bounding-set=-all``: dropping a capability
        # from the BOUNDING set requires CAP_SETPCAP. Without it setpriv
        # cleared the inheritable set and left the bounding set intact —
        # the runtime verifier caught CapBnd=0x30c1 (exactly the caps
        # added here) surviving into the agent process, so the wipe this
        # sandbox documents was never happening. SETPCAP is itself in the
        # set that gets wiped.
        '--cap-add', 'SETPCAP',
        '--security-opt', 'no-new-privileges',
        # AppArmor: explicitly pin to docker-default. On hosts where
        # AppArmor is loaded (Ubuntu, Debian) this gives an additional
        # MAC layer that constrains things capabilities don't (e.g.
        # mount points, /sys writes, ptrace beyond same-uid). On
        # hosts without AppArmor (macOS / many distros) Docker
        # silently ignores this flag — no-op, but documents intent.
        '--security-opt', 'apparmor=docker-default',
        # Seccomp: pin the VENDORED profile explicitly rather than
        # inheriting "whatever this daemon calls default". The daemon's
        # default is host-settable (``dockerd --seccomp-profile
        # /some/weak.json``), so a permissive host silently weakened
        # every sandbox and the old "is it unconfined?" check stayed
        # green because the flag was simply absent. Pinning a file we
        # ship makes the enforced syscall set a property of THIS lib,
        # identical on every host. (``seccomp=builtin`` would express
        # the same intent in one word but only exists on Docker >= 25 —
        # on older daemons it is read as a FILENAME and every spawn
        # dies with "opening seccomp profile (builtin) failed".)
        '--security-opt', f'seccomp={_SECCOMP_PROFILE_PATH}',
        '--read-only',                         # rootfs immutable
        # Tmpfs ceilings: bounded against runaway disk fill but
        # generous enough that legitimate tooling (pip wheel
        # extraction, npm tarballs, language-server caches, tar/gzip
        # scratch space) doesn't hit ENOSPC during normal Claude
        # work. Also nosuid+nodev so a crafted setuid binary or
        # device node smuggled into a tmpfs cannot be activated.
        '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m',
        '--tmpfs', '/run:rw,nosuid,nodev,size=4m',
        '--tmpfs', '/var/tmp:rw,nosuid,nodev,size=32m',
        '--shm-size=16m',                      # bound /dev/shm (Claude doesn't use SysV shm)
        '--memory', _MEMORY_LIMIT,
        '--memory-swap', _MEMORY_LIMIT,        # disable swap (= memory) so OOM is hard
        '--pids-limit', _PIDS_LIMIT,
        '--cpus', _CPUS_LIMIT,
        '--ulimit', 'nofile=1024:1024',        # bounded fd count
        '--ulimit', 'nproc=128:128',           # bounded process count
        '--ulimit', 'core=0:0',                # disable core dumps (prevents memory→disk leak on crash)
        # Cap any single file the container writes at 1 GiB. Stops
        # a runaway log / dump from filling the operator's disk via
        # the workspace bind-mount or the .claude tmpfs.
        '--ulimit', 'fsize=1073741824:1073741824',
        # Disable POSIX message queues entirely — Claude doesn't use
        # them and they're a kernel-side data structure with their
        # own attack surface.
        '--ulimit', 'msgqueue=0:0',
        # Bound pending signals + held file locks. Tiny kernel
        # resources whose unbounded growth has historically been
        # used in local DoS PoCs.
        '--ulimit', 'sigpending=8192:8192',
        '--ulimit', 'locks=64:64',
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
        # Then re-mount every ``.git`` inside it READ-ONLY on top. Later
        # binds overlay earlier ones, so the working tree stays writable
        # while git metadata does not. See ``_git_dir_readonly_mounts``.
        *_git_dir_readonly_mounts(workspace),
        # Auth volume: read-only source mount. Entrypoint copies an
        # allowlisted subset of files into the per-task .claude tmpfs.
        # See entrypoint.sh + ``_AUTH_SOURCE_MOUNT`` for the full
        # rationale. ``ro`` prevents this task writing back to the
        # operator's persistent credential store.
        '-v', f'{_AUTH_VOLUME_NAME}:{_AUTH_SOURCE_MOUNT}:ro',
        # Per-task writable .claude — destroyed on container exit so
        # nothing this task does can persist into the next task.
        # ``nosuid``/``nodev`` block setuid binaries / device nodes
        # being smuggled in. Owner is fixed up in entrypoint.sh
        # (chown to claude:users) before Claude is exec'd.
        '--tmpfs', f'{_CLAUDE_HOME}/.claude:rw,nosuid,nodev,size=64m,mode=0700',
        # WORKDIR is the mount root unless the caller mounted something WIDER
        # than the directory the agent should start in. A multi-repo task
        # mounts the whole task folder (so every clone is reachable) but must
        # still land the agent in its primary repo — otherwise widening the
        # mount silently moves the agent's cwd up a level and every relative
        # path it had been using breaks.
        '-w', _container_workdir(workdir_subpath),
    ])
    if proxy_ip:
        argv.extend([
            # The name resolves to the proxy INSIDE the container, so the
            # firewall (which resolves it) allowlists the proxy and the
            # sandbox never learns the real addresses.
            '--add-host', f'{EGRESS_ALLOWED_HOST}:{proxy_ip}',
            '-e', f'{EGRESS_PROXY_ENV_KEY}={proxy_ip}',
        ])
    # NOTE: the SNI proxy (``ensure_egress_proxy`` / ``sni_proxy.py``) is
    # deliberately NOT wired in here yet. Routing the container at a proxy
    # on the sandbox bridge does not work: that bridge runs with
    # ``enable_icc=false`` so containers cannot reach each other — this
    # lib's own inter-container isolation, working as intended, makes an
    # on-bridge proxy unreachable. Making it work needs a design decision
    # (host-side proxy reached via host-gateway, or a per-task network),
    # not another flag, so the IP allowlist stays in force until then.
    # Secrets travel out-of-band: staged as 0600 files in a 0700 dir and
    # bind-mounted read-only, then read into the environment by the
    # entrypoint. ``-e VAR`` pass-through used to be the mechanism; it
    # kept the value out of ``ps`` but wrote it into the container's
    # ``Config.Env``, where ``docker inspect`` hands it to any holder of
    # the docker socket. Docker never sees the value now.
    secret_dir = materialize_env_secrets(resolved_container_name)
    if secret_dir is not None:
        argv.extend(['-v', f'{secret_dir}:{_ENV_SRC_MOUNT}:ro'])
    # JIT image-identity pin: resolve the *current* digest of the tag
    # right now and refer to the image by that exact ID in the docker
    # run argv. Defends against a TOCTOU where someone with
    # local Docker access retags ``kato/claude-sandbox:latest`` to a
    # different image after ``ensure_image`` returned. If the digest
    # can't be resolved we fail closed rather than fall back to the
    # bare tag — losing the integrity check is not acceptable.
    #
    # Distinguish ``missing`` (rebuild) vs ``transient`` (retry) so
    # the operator's response is clear and they don't reach for an
    # insecure bypass env var.
    try:
        digest = _image_digest_strict(image_tag)
    except _DigestLookupError as exc:
        if exc.kind == 'missing':
            raise SandboxError(
                f'sandbox image {image_tag} is missing from the local '
                f'Docker cache: {exc}. Run ``make sandbox-build`` and '
                'retry. (kato refuses to spawn without a JIT-pinned '
                'image digest.)',
            ) from exc
        raise SandboxError(
            f'cannot resolve sandbox image digest for {image_tag} '
            f'(transient): {exc}. The Docker daemon may be busy or '
            'restarting — retry shortly. If this persists, run '
            '``docker info`` to diagnose. (kato refuses to spawn '
            'without a JIT-pinned image digest; do not work around '
            'this with an env-var bypass — investigate the daemon.)',
        ) from exc
    # Reference the image by its ID, NOT by ``tag@sha256:<id>``. Docker's
    # ``name@sha256:`` form resolves a *registry manifest* digest, and a
    # locally-built image has none (``RepoDigests`` is empty), so the
    # tag@id form sent Docker to the registry and every spawn died with
    # "pull access denied for <tag>". A bare image ID resolves locally
    # and pins the exact same bits — it IS the content address of the
    # image config, so the TOCTOU-retag defence this block exists for is
    # fully preserved (a retag cannot change which ID we run).
    argv.append(digest if digest.startswith('sha256:') else f'sha256:{digest.split(":")[-1]}')
    # Defense-in-depth: refuse to ever pass ``--security-opt
    # seccomp=unconfined`` even if a future maintainer copies a bad
    # config. Run this last so the check sees the final argv.
    _assert_seccomp_not_unconfined(argv)
    _assert_seccomp_pinned(argv)
    # Whole-argv invariant check, immediately before the argv is handed
    # to the caller for Popen. Runs on the DOCKER portion only — the
    # inner Claude command is appended after this point so a user
    # prompt can never satisfy (or trip) a docker-flag assertion.
    _assert_isolated_network(argv)
    _assert_sandbox_flags(argv)
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
# Paths the most recent scan of a workspace could NOT inspect. The scanner
# keeps its ``list[str]`` return type (many call sites), so completeness is
# reported alongside rather than by changing every signature.
_LAST_SCAN_SKIPPED: dict[str, list[str]] = {}


def last_scan_gaps(workspace_path: str) -> list[str]:
    """What the last ``scan_workspace_for_secrets`` could not look at."""
    try:
        root = str(Path(workspace_path).resolve())
    except (OSError, RuntimeError):
        return []
    return list(_LAST_SCAN_SKIPPED.get(root, []))


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

# Per-file size cap for the content-pattern scan. Anything bigger is
# almost certainly a binary blob, generated artifact, or vendored
# dependency — none of which are likely places for a hand-pasted
# credential, and reading multi-megabyte files into memory for a
# single grep is wasted work.
_SECRET_SCAN_PER_FILE_BYTES_CAP = 1_048_576  # 1 MiB

# Directories we skip during the content-pattern scan. They contain
# generated artifacts, vendored deps, or VCS internals that legitimately
# carry tokens (npm registry tarballs, git pack files); scanning them
# produces noise without protecting against the actual leak path
# (operator-written secrets in source files).
_CONTENT_SCAN_SKIP_DIRS: frozenset[str] = frozenset({
    '.git', 'node_modules', 'venv', '.venv', '__pycache__',
    'dist', 'build', 'target', '.tox', '.pytest_cache', '.mypy_cache',
})


def scan_workspace_for_secrets(
    workspace_path: str,
    *,
    logger: logging.Logger | None = None,
) -> list[str]:
    """Walk the workspace looking for committed-secret signals.

    Two signals are considered, in order of preference:

      1. **File name match** — the file's name is one of the
         ``_SUSPICIOUS_FILE_NAMES`` / ``_SUSPICIOUS_PATH_SUFFIXES``
         patterns (e.g. ``.env``, ``id_rsa``, ``.aws/credentials``).
         Cheap, broad, false-positive-prone; the operator override
         exists for the false-positive cases.
      2. **File content match** — the file contains a high-confidence
         credential pattern (AWS key id, GitHub token, OpenAI key, …)
         per ``agent_core_lib`` credential patterns. Closes the case
         where a secret is committed to a file with an innocuous
         name (`config.yaml`, a migration, a README). Skipped for
         binary files, files larger than 1 MiB, and directories
         that are known to carry generated tokens (`.git`,
         `node_modules`, `venv`, `dist`, `build`, …).

    Returns the list of relative paths that match (empty if none).
    Each match is annotated in the returned string: file-name matches
    are bare paths; content matches carry a ``(content: <pattern>)``
    suffix so the operator and the audit log can distinguish them.
    """
    from agent_core_lib.agent_core_lib.helpers.credential_patterns import find_credential_patterns

    try:
        root = Path(workspace_path).resolve()
    except (OSError, RuntimeError):
        return []
    if not root.is_dir():
        return []
    findings: list[str] = []
    skipped: list[str] = []
    scanned = 0
    truncated = False
    try:
        for entry in root.rglob('*'):
            scanned += 1
            if scanned > _SECRET_SCAN_FILE_CAP:
                truncated = True
                skipped.append(f'file cap reached at {_SECRET_SCAN_FILE_CAP} entries')
                break
            if not entry.is_file():
                continue
            relative_str = str(entry.relative_to(root))
            # File-name signal first — cheap, no I/O.
            if entry.name in _SUSPICIOUS_FILE_NAMES:
                findings.append(relative_str)
                continue
            matched_suffix = False
            for suffix in _SUSPICIOUS_PATH_SUFFIXES:
                if relative_str == suffix or relative_str.endswith('/' + suffix):
                    findings.append(relative_str)
                    matched_suffix = True
                    break
            if matched_suffix:
                continue
            # Content signal — skip generated / vendored trees, binary
            # files, and large files. Reads at most 1 MiB per file.
            relative_parts = entry.relative_to(root).parts
            if any(part in _CONTENT_SCAN_SKIP_DIRS for part in relative_parts):
                continue
            try:
                if entry.stat().st_size > _SECRET_SCAN_PER_FILE_BYTES_CAP:
                    # Not scanned — record it. A large file is exactly
                    # where a dump of credentials would hide.
                    skipped.append(f'{relative_str} (larger than 1 MiB)')
                    continue
            except OSError:
                skipped.append(f'{relative_str} (stat failed)')
                continue
            try:
                # ``errors='ignore'`` quietly drops bytes that aren't
                # valid UTF-8 — credential patterns are ASCII so this
                # cannot cause a false negative for the patterns we
                # actually look for.
                text = entry.read_text(encoding='utf-8', errors='ignore')
            except (OSError, PermissionError):
                skipped.append(f'{relative_str} (unreadable)')
                continue
            content_findings = find_credential_patterns(text)
            if content_findings:
                # One annotated line per (file, pattern_name) pair so
                # the operator sees every distinct signal. The redacted
                # preview is intentionally NOT included in the workspace
                # findings list — the file path alone is enough to
                # locate the leak; the pattern name is enough to know
                # what was found.
                seen_patterns: set[str] = set()
                for finding in content_findings:
                    if finding.pattern_name in seen_patterns:
                        continue
                    seen_patterns.add(finding.pattern_name)
                    findings.append(
                        f'{relative_str} (content: {finding.pattern_name})'
                    )
    except (OSError, PermissionError) as exc:
        # A subtree we could not walk is UNSCANNED, not clean.
        skipped.append(f'traversal stopped: {exc}')
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
    if skipped and logger is not None:
        logger.warning(
            'sandbox workspace %s: secret scan could not inspect %d path(s) '
            '(e.g. %s). "No findings" from an incomplete scan is not the same '
            'as "no secrets".', root, len(skipped), '; '.join(skipped[:3]),
        )
    _LAST_SCAN_SKIPPED[str(root)] = skipped
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
    gaps = last_scan_gaps(workspace_path)
    if not findings and gaps:
        # THE TRI-STATE. "No findings" from a scan that skipped files is
        # not "clean" — it is "unknown", and reporting it as clean is the
        # same false assurance pattern that let three broken sandbox flags
        # ship green. An incomplete scan needs the operator's explicit
        # acceptance, exactly like a positive finding does.
        if _env_flag_true(env, ALLOW_WORKSPACE_SECRETS_ENV_KEY):
            if logger is not None:
                logger.warning(
                    'workspace secret scan was INCOMPLETE (%d path(s) not '
                    'inspected) — proceeding because %s=true is set',
                    len(gaps), ALLOW_WORKSPACE_SECRETS_ENV_KEY,
                )
            return
        raise SandboxError(
            'refusing to spawn: the workspace secret scan could not inspect '
            f'{len(gaps)} path(s), so "no secrets found" is not a result — '
            f'examples: {"; ".join(gaps[:3])}. Make them readable (or remove '
            f'them), or accept the gap explicitly with '
            f'{ALLOW_WORKSPACE_SECRETS_ENV_KEY}=true.',
        )
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


def kill_container(container_name: str, *, logger=None) -> bool:
    """Best-effort ``docker kill <container_name>``.

    The ONLY reliable way to stop a sandboxed container once the
    wrapping ``docker run`` client process itself had to be
    force-killed. SIGKILL can never be forwarded by any process — that
    is exactly what distinguishes it from SIGTERM, which the attached
    ``docker`` CLI does forward to the container while it's still
    alive. Without this, every task that ignores SIGTERM (routine —
    e.g. mid tool-call) or hits the CLI subprocess timeout leaves its
    container running indefinitely: ``--rm`` only fires on the
    container's OWN clean exit, never as a side effect of the host
    client process dying.

    Never raises — callers use this from a teardown/error path and
    must not have cleanup itself introduce a new failure. Returns
    False (docker missing, container already gone, timeout, ...)
    without treating that as fatal; the operator can still find and
    remove a stray container manually via ``docker ps``.
    """
    name = str(container_name or '').strip()
    if not name:
        return False
    try:
        result = subprocess.run(
            ['docker', 'kill', name],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if logger:
            logger.warning('docker kill %s failed: %s', name, exc)
        return False
    if result.returncode != 0 and logger:
        logger.warning(
            'docker kill %s exited %s: %s',
            name, result.returncode, result.stderr.strip(),
        )
    return result.returncode == 0


_AUDIT_GENESIS_HASH = '0' * 64

# Spawn-rate guard. A buggy task scan loop or a malicious orchestrator
# can otherwise spam ``docker run`` until the host is wedged. The
# limit is generous for legitimate parallelism (a ~3-task pipeline
# spinning up retries) but catches a runaway. Counts entries in the
# audit log within ``_SPAWN_RATE_WINDOW_SEC``; refuses if at/over
# ``_SPAWN_RATE_LIMIT``.
_SPAWN_RATE_WINDOW_SEC = 60
_SPAWN_RATE_LIMIT = 30


_AUDIT_KEY_FILENAME = 'sandbox-audit.key'


def _audit_key_path() -> Path:
    """Where the audit MAC key lives — beside the log, never inside it."""
    return _DEFAULT_AUDIT_LOG_PATH.parent / _AUDIT_KEY_FILENAME


def _audit_key(*, create: bool = True) -> bytes:
    """The HMAC key, created 0600 on first use. ``b''`` when unavailable.

    HONEST SCOPE: this key sits on the same host, readable by the same
    user the agent's owner runs as. It defeats a tamperer who edits the
    log file — recomputing a valid chain now also requires the key — but
    it does NOT defeat an attacker who already has that user's
    filesystem access. Tamper-evidence you can lean on needs entries
    shipped append-only off this machine; that is a deployment decision,
    not something this function can fake.
    """
    path = _audit_key_path()
    try:
        if path.is_file():
            return path.read_bytes().strip()
        if not create:
            return b''
        path.parent.mkdir(parents=True, exist_ok=True)
        key = os.urandom(32).hex().encode('ascii')
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, 'wb') as stream:
            stream.write(key)
        return key
    except OSError:
        return b''


def _audit_mac(key: bytes, payload: bytes) -> str:
    """HMAC-SHA256 of one raw entry line."""
    import hmac
    return hmac.new(key, payload, hashlib.sha256).hexdigest() if key else ''


def _last_audit_chain_hash(target: Path) -> str:
    """Return ``sha256(last_line_text)`` of the audit log, or genesis.

    The chain hash is built over the raw bytes of each prior line
    *including* its own ``prev_hash`` field, so any single-entry
    edit invalidates every subsequent entry's chain link. Operators
    can verify the chain offline with ``sha256sum`` per line.

    NOTE: callers that need the read+write to be atomic across
    parallel spawns must wrap this call (and the subsequent write)
    in ``exclusive_file_lock(target)``. ``record_spawn`` does that.
    """
    if not target.exists():
        return _AUDIT_GENESIS_HASH
    try:
        with target.open('rb') as fh:
            try:
                fh.seek(-4096, os.SEEK_END)
            except OSError:
                fh.seek(0)
            tail = fh.read()
    except OSError:
        return _AUDIT_GENESIS_HASH
    lines = [ln for ln in tail.splitlines() if ln.strip()]
    if not lines:
        return _AUDIT_GENESIS_HASH
    return hashlib.sha256(lines[-1]).hexdigest()


def verify_audit_chain(
    target: Path | None = None,
    *,
    logger: logging.Logger | None = None,
) -> dict:
    """Walk the audit log and check every chain link.

    The log is hash-chained precisely so tampering is detectable — but
    nothing ever checked it, which makes the property theoretical: an
    edited or truncated history looks identical to an honest one until
    somebody manually runs sha256sum over the file. This does that walk.

    Each line carries ``prev_hash`` = sha256 of the PREVIOUS raw line
    (genesis for the first), so editing entry N invalidates the link at
    N+1. Returns ``{ok, entries, broken_at, error}``; ``broken_at`` is
    the 1-based line number of the first bad link. Read-only, and never
    raises — a verifier that can take down the boot it is auditing is
    worse than the tampering it looks for.

    KNOWN RESIDUAL: dropping entries from the END leaves a shorter but
    internally consistent chain, and no hash chain can detect that on its
    own — the check would need an external anchor (the last known hash
    and count, held somewhere the log's writer cannot reach). Edits and
    reordering ARE caught. The error text says only what is true.
    """
    path = target or _DEFAULT_AUDIT_LOG_PATH
    result = {'ok': True, 'entries': 0, 'broken_at': 0, 'error': ''}
    if not path.exists():
        return result
    try:
        with exclusive_file_lock(path):
            with path.open('rb') as handle:
                lines = [ln for ln in handle.read().splitlines() if ln.strip()]
    except OSError as exc:
        result['error'] = f'cannot read audit log: {exc}'
        return result
    expected = _AUDIT_GENESIS_HASH
    key = _audit_key(create=False)
    for index, raw in enumerate(lines, start=1):
        try:
            entry = json.loads(raw)
        except ValueError:
            result.update(ok=False, broken_at=index, error='entry is not valid JSON')
            break
        recorded_mac = str(entry.pop('mac', ''))
        if recorded_mac:
            body = json.dumps(entry, ensure_ascii=False, sort_keys=True)
            if not hmac.compare_digest(
                recorded_mac, _audit_mac(key, body.encode('utf-8')),
            ):
                result.update(
                    ok=False, broken_at=index,
                    error='entry MAC does not verify — the line was altered, '
                          'or was written with a different key',
                )
                break
        elif key:
            result.update(
                ok=False, broken_at=index,
                error='entry carries no MAC but a signing key exists — the '
                      'line was not written by this installation',
            )
            break
        if str(entry.get('prev_hash', '')) != expected:
            result.update(
                ok=False, broken_at=index,
                error='chain link does not match the previous entry — the log '
                      'was edited or reordered',
            )
            break
        expected = hashlib.sha256(raw).hexdigest()
    result['entries'] = len(lines)
    if not result['ok'] and logger is not None:
        logger.error(
            'sandbox audit chain FAILED verification at entry %s of %s: %s '
            '(%s)', result['broken_at'], result['entries'], result['error'], path,
        )
    elif logger is not None and result['entries']:
        logger.info(
            'sandbox audit chain verified: %s entries intact', result['entries'],
        )
    return result


def _count_recent_spawns(target: Path, *, now: datetime | None = None) -> int:
    """Count audit-log entries within ``_SPAWN_RATE_WINDOW_SEC``.

    Helper — does NOT take the audit lock. Callers that need the
    count to be consistent with a subsequent write must hold
    ``exclusive_file_lock(target)`` themselves. ``record_spawn``
    does this; the standalone ``check_spawn_rate`` below also takes
    the lock for its read-only callers.
    """
    if not target.exists():
        return 0
    cutoff = (now or datetime.now(timezone.utc)).timestamp() - _SPAWN_RATE_WINDOW_SEC
    count = 0
    try:
        with target.open('rb') as fh:
            try:
                fh.seek(-65536, os.SEEK_END)
            except OSError:
                fh.seek(0)
            tail = fh.read().decode('utf-8', errors='replace')
    except OSError:
        return 0
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = entry.get('timestamp', '')
        try:
            entry_time = datetime.fromisoformat(ts).timestamp()
        except (ValueError, TypeError):
            continue
        if entry_time >= cutoff:
            count += 1
    return count


def check_spawn_rate(
    audit_log_path: Path | None = None,
    *,
    now: datetime | None = None,
) -> int:
    """Lock-protected variant of ``_count_recent_spawns`` for external callers.

    Raises ``SandboxError`` if the count is at or above the limit. The
    authoritative atomic check is inside ``record_spawn`` (so parallel
    spawns can't both see ``N-1`` and both proceed); this function
    exists for callers (UI / tooling) that want a peek without writing.
    """
    target = audit_log_path or _DEFAULT_AUDIT_LOG_PATH
    with exclusive_file_lock(target):
        count = _count_recent_spawns(target, now=now)
    if count >= _SPAWN_RATE_LIMIT:
        raise SandboxError(
            f'sandbox spawn rate exceeded: {count} spawns in the last '
            f'{_SPAWN_RATE_WINDOW_SEC}s (limit {_SPAWN_RATE_LIMIT}). '
            'Refusing to launch — investigate the caller.',
        )
    return count


# Operator override: when set, a failure to append to the audit log
# *blocks the spawn* instead of just warning. Default-off so a stuck
# disk doesn't take kato down on a normal box; safety-conscious
# operators can opt into "no audit, no spawn".
AUDIT_REQUIRED_ENV_KEY = 'KATO_SANDBOX_AUDIT_REQUIRED'


def record_spawn(
    *,
    task_id: str,
    container_name: str,
    workspace_path: str,
    image_tag: str = SANDBOX_IMAGE_TAG,
    audit_log_path: Path | None = None,
    logger: logging.Logger | None = None,
    env: dict | None = None,
) -> None:
    """Append one JSON line per sandboxed spawn to the audit log.

    Each line embeds ``prev_hash`` = ``sha256`` of the previous line's
    raw bytes, so any single-entry edit invalidates the chain from
    that point forward. Verifiable offline with ``sha256sum`` — no
    secret needed for tamper-evidence.

    Best-effort by default: a write failure logs to stderr + warning
    but does not abort the spawn (a stuck disk shouldn't take kato
    down). Operators can flip this to fail-closed by setting
    ``KATO_SANDBOX_AUDIT_REQUIRED=true``, in which case any audit
    write failure raises ``SandboxError`` and the spawn is refused.
    """
    target = audit_log_path or _DEFAULT_AUDIT_LOG_PATH
    # Hold the audit lock for the entire critical section: count
    # recent spawns → check rate limit → read prev_hash → write
    # entry → fsync. Without this, two parallel spawns can each see
    # ``N-1`` recent entries (admitting one over the limit) AND each
    # compute their ``prev_hash`` against the same predecessor
    # (leaving one chain link invalid). Per-file lock via
    # ``<path>.lock``.
    try:
        with exclusive_file_lock(target):
            recent = _count_recent_spawns(target)
            if recent >= _SPAWN_RATE_LIMIT:
                raise SandboxError(
                    f'sandbox spawn rate exceeded: {recent} spawns in '
                    f'the last {_SPAWN_RATE_WINDOW_SEC}s (limit '
                    f'{_SPAWN_RATE_LIMIT}). Refusing to launch — '
                    'investigate the caller.',
                )
            prev_hash = _last_audit_chain_hash(target)
            entry = {
                'timestamp': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'event': 'spawn',
                'task_id': str(task_id or ''),
                'container_name': container_name,
                'image_tag': image_tag,
                'image_digest': _image_digest(image_tag) or '',
                'workspace_path': workspace_path,
                'prev_hash': prev_hash,
            }
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(target.parent, 0o700)
            except OSError:
                pass
            fd = os.open(
                str(target),
                os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                0o600,
            )
            try:
                # MAC over the entry as written, then appended to it. The
                # chain alone proves internal consistency, which anyone who
                # can edit the file can re-establish; the MAC means doing so
                # also requires the key, which lives outside the log dir.
                body = json.dumps(entry, ensure_ascii=False, sort_keys=True)
                mac = _audit_mac(_audit_key(), body.encode('utf-8'))
                entry['mac'] = mac
                line = (json.dumps(entry, ensure_ascii=False) + '\n').encode('utf-8')
                os.write(fd, line)
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                dir_fd = os.open(str(target.parent), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
    except SandboxError:
        raise
    except OSError as exc:
        if _env_flag_true(env, AUDIT_REQUIRED_ENV_KEY):
            raise SandboxError(
                f'failed to write sandbox audit log entry to {target}: '
                f'{exc} — refusing to spawn ({AUDIT_REQUIRED_ENV_KEY}=true)'
            ) from exc
        msg = (
            f'[kato-sandbox] WARNING: failed to write sandbox audit '
            f'log entry to {target}: {exc} — spawn proceeded but is '
            f'NOT recorded in the audit trail. Set '
            f'{AUDIT_REQUIRED_ENV_KEY}=true to fail-close on this.'
        )
        sys.stderr.write(msg + '\n')
        sys.stderr.flush()
        if logger is not None:
            logger.warning(msg)

    # External audit-log shipping (OG2). Best-effort by default;
    # operators who want fail-closed shipping set
    # ``KATO_SANDBOX_AUDIT_SHIP_REQUIRED=true``. Runs AFTER the local
    # write so the local log is the authoritative copy and a sink
    # failure can never lose the entry. Closes the tail-truncation
    # residual: an external sink is the operator's reference for
    # "did the local file lose entries" verification.
    from sandbox_core_lib.sandbox_core_lib.audit_log_shipping import (
        AuditShipError, ship_audit_entry,
    )
    try:
        ship_audit_entry(entry, env=env, logger=logger)
    except AuditShipError as exc:
        # Only re-raised when ``KATO_SANDBOX_AUDIT_SHIP_REQUIRED=true`` —
        # ``ship_audit_entry`` already swallows otherwise.
        raise SandboxError(
            f'audit-log shipping failed: {exc} — refusing to spawn '
            f'(KATO_SANDBOX_AUDIT_SHIP_REQUIRED=true)'
        ) from exc


class _DigestLookupError(RuntimeError):
    """Internal: distinguishes 'no such image' from 'daemon transient'.

    ``kind`` is one of ``'missing'`` (rebuild fixes it) or
    ``'transient'`` (retry fixes it). Callers that need a clean
    operator diagnostic — e.g. ``wrap_command`` — can branch on
    this; older callers that just want a string treat any failure
    as empty.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _image_digest(image_tag: str) -> str:
    """Best-effort: return the local image digest, empty string on failure."""
    try:
        return _image_digest_strict(image_tag)
    except _DigestLookupError:
        return ''


def _image_digest_strict(image_tag: str) -> str:
    """Like ``_image_digest`` but raises ``_DigestLookupError`` on failure.

    Distinguishes the two operationally distinct failure modes:

    * ``missing``    — daemon answered, image not present. Fix: rebuild.
    * ``transient``  — daemon couldn't be reached or timed out. Fix: retry.

    Used by ``wrap_command`` so the operator sees a diagnostic that
    points at the actual remedy instead of a generic "digest
    unresolvable" that invites them to add an insecure bypass env var.
    """
    try:
        result = subprocess.run(
            [
                'docker', 'image', 'inspect',
                '--format', '{{ index .Id }}',
                image_tag,
            ],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=5,
        )
    except subprocess.TimeoutExpired as exc:
        raise _DigestLookupError(
            'transient',
            f'docker image inspect timed out for {image_tag} ({exc}); '
            'daemon may be busy — retry, then check ``docker info``',
        ) from exc
    except OSError as exc:
        raise _DigestLookupError(
            'transient',
            f'cannot invoke docker for {image_tag} ({exc}); '
            'daemon may be down — start docker and retry',
        ) from exc
    if result.returncode != 0:
        stderr = (result.stderr or '').strip().lower()
        if 'no such image' in stderr or 'not found' in stderr:
            raise _DigestLookupError(
                'missing',
                f'image {image_tag} not present in local cache; '
                'run ``make sandbox-build`` to (re)build it',
            )
        raise _DigestLookupError(
            'transient',
            f'docker image inspect for {image_tag} returned '
            f'rc={result.returncode}: {stderr or "(no stderr)"}',
        )
    digest = result.stdout.strip()
    if not digest:
        raise _DigestLookupError(
            'transient',
            f'docker returned an empty digest for {image_tag}',
        )
    return digest


def _assert_seccomp_not_unconfined(argv: list[str]) -> None:
    """Refuse the spawn if any flag in ``argv`` disables seccomp.

    Docker's default seccomp profile is a meaningful additional syscall
    blockade on top of cap-drop ALL + bounding-set wipe (e.g. it blocks
    ``unshare(CLONE_NEWUSER)`` for non-privileged containers, which
    historically hosted a stream of kernel CVEs). Any future change
    that adds ``--security-opt seccomp=unconfined`` silently downgrades
    the security model — fail closed instead.
    """
    for i, tok in enumerate(argv):
        flat = tok
        if i + 1 < len(argv) and tok == '--security-opt':
            flat = argv[i + 1]
        if 'seccomp=unconfined' in flat:
            raise SandboxError(
                'sandbox argv contains seccomp=unconfined — refusing '
                'to spawn. The default seccomp profile is required.',
            )


MAX_CONTAINER_LIFETIME_ENV_KEY = 'AGENT_SANDBOX_MAX_CONTAINER_SECONDS'
# 8h: far longer than any real turn, short enough that a wedged container
# does not hold a workspace mount and its credentials open for days. A
# session that legitimately needs longer is better served by restarting
# it than by an agent process nobody has looked at since yesterday.
_DEFAULT_MAX_CONTAINER_LIFETIME_SECONDS = 8 * 60 * 60


def _max_container_lifetime_seconds() -> float:
    """Cap on how long one sandbox container may live. 0 disables."""
    raw = os.environ.get(MAX_CONTAINER_LIFETIME_ENV_KEY, '')
    if not str(raw).strip():
        return float(_DEFAULT_MAX_CONTAINER_LIFETIME_SECONDS)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return float(_DEFAULT_MAX_CONTAINER_LIFETIME_SECONDS)


def _detached_process_kwargs() -> dict:
    """Popen kwargs that detach a child from this process's signal group."""
    if sys.platform == 'win32':                  # pragma: no cover - platform
        creation_flags = 0
        for name in ('DETACHED_PROCESS', 'CREATE_NEW_PROCESS_GROUP'):
            creation_flags |= getattr(subprocess, name, 0)
        return {'creationflags': creation_flags}
    return {'start_new_session': True}


class WatchdogHandle(object):
    """Live parent-loss watchdog for one container.

    Holds the WRITE end of the pipe whose read end the watchdog process
    is blocked on. The guarantee comes from the kernel, not from code
    running at the right moment: however this process dies — SIGKILL,
    segfault, OOM, power loss — the descriptor closes and the watchdog
    observes EOF. There is no shutdown path to forget to call.
    """

    def __init__(self, process, write_fd: int, container_name: str) -> None:
        self._process = process
        self._write_fd = write_fd
        self.container_name = container_name
        self._closed = False

    @property
    def pid(self) -> int:
        return getattr(self._process, 'pid', 0)

    def disarm(self, *, timeout: float = 5.0) -> None:
        """Tell the watchdog this was a clean shutdown; it must not reap."""
        if self._closed:
            return
        self._closed = True
        try:
            os.write(self._write_fd, watchdog_module.DISARM_BYTE)
        except OSError:
            pass
        try:
            os.close(self._write_fd)
        except OSError:
            pass
        try:
            self._process.wait(timeout=timeout)
        except Exception:
            # A watchdog that will not exit is not worth blocking the
            # caller for; it re-checks ownership before acting, and the
            # container is gone by now, so it will no-op and exit.
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.disarm()
        return False


def arm_container_watchdog(
    container_name: str,
    *,
    workspace_path: str = '',
    logger: logging.Logger | None = None,
):
    """Start a watchdog that reaps ``container_name`` if THIS process dies.

    Returns a ``WatchdogHandle`` (call ``disarm()`` on clean shutdown),
    or ``None`` when the watchdog could not be started — the boot-time
    sweep still covers that case, so failing to arm must never block a
    spawn.
    """
    read_fd, write_fd = os.pipe()
    incident_path = str(_DEFAULT_AUDIT_LOG_PATH.parent / 'sandbox-incidents.log')
    try:
        os.set_inheritable(read_fd, True)
        process = subprocess.Popen(
            [
                sys.executable, '-m', 'sandbox_core_lib.sandbox_core_lib.watchdog',
                '--fd', str(read_fd),
                '--container', container_name,
                '--owner-pid', str(os.getpid()),
                '--owner-boot', host_boot_identity(),
                '--incident-path', incident_path,
                # Label keys are owned here, not baked into the watchdog.
                '--pid-label', _OWNER_PID_LABEL,
                '--boot-label', _OWNER_BOOT_LABEL,
                '--max-lifetime-seconds', str(_max_container_lifetime_seconds()),
                # Total-size ceilings: the container's per-file ulimit
                # stops one huge file, not a million small ones.
                '--workspace', str(workspace_path or ''),
            ],
            pass_fds=(read_fd,),
            # Detach from this process's signal group. A terminal group
            # signal aimed at the owner (Ctrl-C, a closing shell) must not
            # also kill the watchdog — that is precisely when it has work
            # to do. ``start_new_session`` is POSIX-only; Windows needs
            # the equivalent creation flags instead, and passing the POSIX
            # kwarg there raises.
            **_detached_process_kwargs(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        for fd in (read_fd, write_fd):
            try:
                os.close(fd)
            except OSError:
                pass
        if logger is not None:
            logger.warning(
                'could not arm the sandbox watchdog for %s (%s); the '
                'boot-time sweep remains the fallback', container_name, exc,
            )
        return None
    # The parent must not keep the read end open: while ANY process holds
    # it, the watchdog's read never sees EOF and a dead owner would go
    # unnoticed — the exact failure this class exists to prevent.
    try:
        os.close(read_fd)
    except OSError:
        pass
    if logger is not None:
        logger.info(
            'sandbox watchdog armed for %s (pid %s)',
            container_name, process.pid,
        )
    return WatchdogHandle(process, write_fd, container_name)


_EGRESS_PROXY_IMAGE = 'python:3.11-slim'
_EGRESS_PROXY_PORT = 443
EGRESS_PROXY_ENV_KEY = 'AGENT_SANDBOX_EGRESS_PROXY_IP'
EGRESS_ALLOWED_HOST = 'api.anthropic.com'


def _task_network_name(container_name: str) -> str:
    return f'{container_name}-net'


def _task_proxy_name(container_name: str) -> str:
    return f'{container_name}-proxy'


def _container_ip_on(container: str, network: str) -> str:
    try:
        result = subprocess.run(
            [
                'docker', 'inspect', container, '--format',
                '{{with index .NetworkSettings.Networks "' + network + '"}}{{.IPAddress}}{{end}}',
            ],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ''
    return result.stdout.strip() if result.returncode == 0 else ''


def _wait_for_proxy_listening(
    proxy: str,
    *,
    timeout: float = 20.0,
    logger: logging.Logger | None = None,
) -> bool:
    """Poll until the proxy accepts connections on its port."""
    probe = (
        'import socket,sys;'
        's=socket.create_connection(("127.0.0.1",%d),timeout=1);s.close()'
        % _EGRESS_PROXY_PORT
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ['docker', 'exec', proxy, 'python3', '-c', probe],
                capture_output=True, timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode == 0:
            return True
        time.sleep(0.25)
    if logger is not None:
        logger.warning('egress proxy %s never started listening', proxy)
    return False


def start_task_egress_proxy(
    container_name: str,
    *,
    owner_labels: bool = True,
    logger: logging.Logger | None = None,
) -> tuple[str, str]:
    """Private network + SNI proxy for ONE sandbox. ``(network, proxy_ip)``.

    Topology, and why it is shaped this way:

        sandbox ──(private 2-member network)── proxy ──(sandbox bridge)── internet

    The sandbox joins ONLY the private network, so it has no route to the
    internet at all; its single reachable peer is the proxy. The proxy is
    two-legged: it also sits on the main sandbox bridge, which is where
    its own outbound traffic goes.

    That arrangement is what makes hostname pinning enforceable. The old
    single-bridge design allowlisted the IP addresses ``api.anthropic.com``
    resolved to — shared cloud addresses, so anything else behind them was
    reachable and the client picked its own hostname. Here the sandbox
    never learns those addresses: ``api.anthropic.com`` is pointed at the
    proxy, and the proxy enforces the name from the TLS ClientHello.

    The private network has exactly two members, so inter-container
    communication on it grants precisely the one conversation intended —
    the main bridge keeps ``enable_icc=false`` and sandboxes still cannot
    reach each other.

    Returns ``('', '')`` on any failure; the caller then falls back to the
    plain bridge, because a spawn that cannot start is worse than one
    running under the older, weaker egress rule.
    """
    network = _task_network_name(container_name)
    proxy = _task_proxy_name(container_name)
    labels: list[str] = []
    if owner_labels:
        labels = [
            '--label', f'{_IMAGE_IDENTITY_LABEL}={_IMAGE_IDENTITY_VALUE}',
            '--label', f'{_OWNER_PID_LABEL}={os.getpid()}',
            '--label', f'{_OWNER_BOOT_LABEL}={host_boot_identity()}',
        ]
    proxy_source = Path(__file__).resolve().parent / 'sni_proxy.py'
    # Resolve on the HOST, where DNS works, and hand the address to the
    # proxy. Nothing inside either container then performs a lookup.
    upstream_args: list[str] = []
    try:
        import socket as _socket
        upstream_args = [
            '--upstream',
            f'{EGRESS_ALLOWED_HOST}={_socket.gethostbyname(EGRESS_ALLOWED_HOST)}',
        ]
    except OSError:
        upstream_args = []
    try:
        ensure_network(logger=logger)
        created = subprocess.run(
            [
                'docker', 'network', 'create', '--driver', 'bridge',
                # ``--internal`` is what makes the "no route to the
                # internet" claim TRUE rather than aspirational. Without
                # it this is an ordinary NAT-ed bridge and the sandbox can
                # reach anything directly — the in-container firewall was
                # the only thing stopping it, which is precisely the
                # single layer this topology is supposed to be independent
                # of. The proxy still reaches out via its SECOND leg on
                # the routed bridge.
                '--internal',
                *labels, network,
            ],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=60,
        )
        if created.returncode != 0 and 'already exists' not in (created.stderr or ''):
            raise SandboxError((created.stderr or '').strip()[:200])
        started = subprocess.run(
            [
                'docker', 'run', '-d',
                '--name', proxy,
                # ROUTED network FIRST. A container keeps the default
                # route of the network it was CREATED on, and
                # ``docker network connect`` never adds one — so starting
                # the proxy on the ``--internal`` bridge left it with no
                # route out at all: every upstream lookup died with
                # "Temporary failure in name resolution". Created here,
                # attached to the private bridge below.
                '--network', _SANDBOX_NETWORK_NAME,
                *labels,
                # The proxy needs no privileges beyond binding :443 in its
                # own namespace.
                '--cap-drop', 'ALL',
                '--cap-add', 'NET_BIND_SERVICE',
                '--security-opt', 'no-new-privileges',
                '--read-only',
                '--tmpfs', '/tmp:rw,nosuid,nodev,size=8m',
                '--memory', '128m', '--pids-limit', '64',
                '-v', f'{proxy_source}:/app/sni_proxy.py:ro',
                _EGRESS_PROXY_IMAGE,
                'python3', '/app/sni_proxy.py',
                '--port', str(_EGRESS_PROXY_PORT),
                '--allow', EGRESS_ALLOWED_HOST,
                *upstream_args,
            ],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=120,
        )
        if started.returncode != 0:
            raise SandboxError((started.stderr or '').strip()[:200])
        # Second leg: the private side the sandbox talks to.
        attached = subprocess.run(
            ['docker', 'network', 'connect', network, proxy],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=60,
        )
        if attached.returncode != 0:
            raise SandboxError((attached.stderr or '').strip()[:200])
    except (OSError, subprocess.TimeoutExpired, SandboxError) as exc:
        remove_task_egress_proxy(container_name)
        # FAIL CLOSED. This used to fall back to the address-based
        # allowlist, which meant a proxy that failed to start silently
        # downgraded egress to the weaker rule the proxy exists to
        # replace — a security property you cannot rely on is not one.
        # Same reasoning as the gVisor requirement: refuse rather than
        # quietly run with less.
        raise SandboxError(
            f'cannot start the per-task egress proxy for {container_name}: '
            f'{exc}. Refusing to spawn — falling back to the address-based '
            f'allowlist would silently drop hostname pinning.',
        ) from exc
    address = _container_ip_on(proxy, network)
    if not address:
        remove_task_egress_proxy(container_name)
        raise SandboxError(
            f'egress proxy for {container_name} has no address on {network}',
        )
    # WAIT for the listener. The sandbox starts immediately after this
    # returns and its firewall self-check dials the proxy straight away —
    # without this the spawn races the interpreter's startup and fails
    # closed with "cannot reach api.anthropic.com", which reads like a
    # network fault rather than a few hundred milliseconds of Python.
    if not _wait_for_proxy_listening(proxy, logger=logger):
        remove_task_egress_proxy(container_name)
        raise SandboxError(
            f'egress proxy for {container_name} never began listening',
        )
    if logger is not None:
        logger.info(
            'egress for %s is pinned to %s via its own proxy at %s',
            container_name, EGRESS_ALLOWED_HOST, address,
        )
    return network, address


def remove_task_egress_proxy(container_name: str) -> None:
    """Tear down one sandbox's proxy container and private network."""
    for command in (
        ['docker', 'rm', '-f', _task_proxy_name(container_name)],
        ['docker', 'network', 'rm', _task_network_name(container_name)],
    ):
        try:
            subprocess.run(command, capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue


def _backstop_tag() -> str:
    """Comment tag identifying this lib's host rules (derived, not literal)."""
    return f'{_SANDBOX_NETWORK_NAME}-backstop'


def _sandbox_bridge_subnet() -> str:
    """CIDR of the isolated sandbox bridge, or '' when undiscoverable."""
    try:
        result = subprocess.run(
            [
                'docker', 'network', 'inspect', _SANDBOX_NETWORK_NAME,
                '--format', '{{range .IPAM.Config}}{{.Subnet}} {{end}}',
            ],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ''
    if result.returncode != 0:
        return ''
    subnets = result.stdout.split()
    return subnets[0] if subnets else ''


def _run_host_netfilter_script(script: str, *, timeout: int = 120):
    """Run an iptables script in the HOST network namespace.

    Uses a throwaway privileged container on ``--net=host`` because the
    host netfilter tables are not reachable from an ordinary container —
    that inaccessibility is exactly the property that makes a host-side
    rule worth having. The image is the sandbox image itself: already
    digest-pinned and built by us, so this introduces no new supply-chain
    surface. Docker Desktop keeps its chains in the LEGACY tables, so the
    script prefers ``iptables-legacy`` and falls back to ``iptables``.
    """
    wrapper = (
        'if command -v iptables-legacy >/dev/null 2>&1; then IPT=iptables-legacy; '
        'else IPT=iptables; fi\n' + script
    )
    # NOT ``--privileged``. The first version of this helper ran the
    # sandbox image with full privileges on the host network, which
    # promoted any compromise of that image into a privileged host-network
    # container — a much worse outcome than the leak the backstop exists
    # to prevent. Editing netfilter needs exactly CAP_NET_ADMIN (plus
    # NET_RAW for iptables' own socket); everything else is dropped, and
    # the image is pinned to the ID we resolved rather than a mutable tag.
    try:
        image = _image_digest_strict(SANDBOX_IMAGE_TAG)
    except _DigestLookupError:
        image = SANDBOX_IMAGE_TAG
    return subprocess.run(
        [
            'docker', 'run', '--rm',
            '--net=host',
            '--cap-drop', 'ALL',
            '--cap-add', 'NET_ADMIN',
            '--cap-add', 'NET_RAW',
            '--security-opt', 'no-new-privileges',
            '--read-only',
            # iptables takes /run/xtables.lock; a read-only rootfs without
            # this makes every rule edit fail with "can't open lock file".
            '--tmpfs', '/run:rw,nosuid,nodev,size=1m',
            '--entrypoint', 'sh', image, '-c', wrapper,
        ],
        capture_output=True, text=True,
        encoding='utf-8', errors='replace',
        timeout=timeout,
    )


def _backstop_chain() -> str:
    """Dedicated chain holding this lib's rules.

    Rules live in their OWN chain rather than directly in DOCKER-USER so
    that re-installing is a flush, not a surgical delete. The first
    version tried to clean up with ``iptables -D ... -m comment
    --comment <tag>``, which never deletes anything: ``-D`` matches on
    the FULL rule spec, so a comment-only spec matches no rule, the
    delete loop exited immediately, and every install stacked another
    generation onto the chain.
    """
    return _backstop_tag().upper()


def remove_host_egress_backstop(*, logger: logging.Logger | None = None) -> bool:
    """Detach and empty this lib's chain on the host."""
    chain = _backstop_chain()
    script = (
        f'while $IPT -D DOCKER-USER -j {chain} 2>/dev/null; do :; done\n'
        f'$IPT -F {chain} 2>/dev/null || true\n'
        f'$IPT -X {chain} 2>/dev/null || true\n'
        'exit 0\n'
    )
    try:
        result = _run_host_netfilter_script(script)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if logger is not None:
            logger.warning('could not remove host egress backstop: %s', exc)
        return False
    return result.returncode == 0


def install_host_egress_backstop(*, logger: logging.Logger | None = None) -> bool:
    """Install a host-side egress floor for the sandbox bridge.

    The in-container firewall is the PRIMARY egress control and does the
    precise work (destination allowlist, DNS rate limiting, RFC1918 and
    metadata denies). Its weakness is placement: it lives in the same
    network namespace as the workload, so its integrity depends entirely
    on the capability drop having actually happened — an assumption that
    was false in this sandbox until the runtime verifier caught it.

    This adds a floor the container cannot reach at all, in the HOST's
    ``DOCKER-USER`` chain, scoped to the sandbox bridge subnet so no
    other network is affected. Even with the in-container rules flushed,
    a sandbox can only emit TCP/443 and DNS to the pinned resolvers.

    Deliberately port/protocol-scoped rather than destination-pinned: the
    resolved addresses of the allowlisted host rotate, and a stale host
    rule would break a legitimate session. Destination pinning stays
    inside the container, where it is re-resolved on every start.

    Best-effort by design — a backstop that refuses to boot the product
    when it cannot be installed would be worse than the exposure it
    closes. Returns True when the rules are in place.
    """
    # Create the isolated bridge FIRST. At boot this runs before anything
    # has spawned, so the network often does not exist yet — the backstop
    # would look up an empty subnet and silently skip, leaving the host
    # layer absent exactly when the first task starts.
    try:
        ensure_network(logger=logger)
    except SandboxError as exc:
        if logger is not None:
            logger.warning(
                'host egress backstop skipped: sandbox network unavailable (%s)', exc,
            )
        return False
    subnet = _sandbox_bridge_subnet()
    if not subnet:
        if logger is not None:
            logger.warning(
                'host egress backstop skipped: cannot determine the %s subnet',
                _SANDBOX_NETWORK_NAME,
            )
        return False
    tag = _backstop_tag()
    chain = _backstop_chain()
    comment = f'-m comment --comment {tag}'
    # Appended in evaluation order inside our own chain.
    rules = [
        f'-m conntrack --ctstate ESTABLISHED,RELATED -j RETURN {comment}',
        f'-p tcp --dport 443 -j RETURN {comment}',
        f'-p udp --dport 53 -d 1.1.1.1/32 -j RETURN {comment}',
        f'-p udp --dport 53 -d 1.0.0.1/32 -j RETURN {comment}',
        f'-p tcp --dport 53 -d 1.1.1.1/32 -j RETURN {comment}',
        f'-p tcp --dport 53 -d 1.0.0.1/32 -j RETURN {comment}',
        f'-j DROP {comment}',
    ]
    script = [
        # Idempotent by construction: our rules live in a chain we own, so
        # re-installing FLUSHES rather than trying to delete rule-by-rule.
        f'$IPT -N {chain} 2>/dev/null || true',
        f'$IPT -F {chain}',
    ]
    script += [f'$IPT -A {chain} {rule}' for rule in rules]
    script += [
        # Exactly one jump from DOCKER-USER, scoped to the sandbox bridge.
        f'while $IPT -D DOCKER-USER -s {subnet} -j {chain} 2>/dev/null; do :; done',
        f'$IPT -I DOCKER-USER 1 -s {subnet} -j {chain}',
        f'$IPT -S DOCKER-USER | grep -c "j {chain}"',
    ]
    try:
        result = _run_host_netfilter_script('\n'.join(script))
    except (OSError, subprocess.TimeoutExpired) as exc:
        if logger is not None:
            logger.warning('host egress backstop not installed: %s', exc)
        return False
    if result.returncode != 0:
        if logger is not None:
            logger.warning(
                'host egress backstop not installed (rc=%s): %s. The '
                'in-container firewall still applies.',
                result.returncode, (result.stderr or '').strip()[:200],
            )
        return False
    if logger is not None:
        logger.info(
            'host egress backstop active on %s (%s): only TCP/443 and DNS '
            'to the pinned resolvers can leave the sandbox bridge, even if '
            'a container flushes its own rules',
            _SANDBOX_NETWORK_NAME, subnet,
        )
    return True


def _secret_dir_root() -> Path:
    """Root for per-spawn secret drops (derived, not a second literal)."""
    return _DEFAULT_AUDIT_LOG_PATH.parent / 'sandbox-env'


def materialize_env_secrets(container_name: str) -> Path | None:
    """Stage pass-through secrets as files for an out-of-band mount.

    Replaces ``docker run -e VAR``. The pass-through form (``-e NAME``
    with no value) already kept the secret out of the docker ARGV — it
    never showed up in ``ps`` — but the value still landed in the
    container's ``Config.Env``, which means ``docker inspect`` handed
    ``ANTHROPIC_API_KEY=sk-...`` to anyone holding the docker socket, and
    it stayed there for the container's whole lifetime.

    Instead the values are written to a 0600 file in a 0700 per-spawn
    directory, bind-mounted read-only, and read back by the entrypoint
    into the process environment. Docker never learns the value, so no
    daemon-side metadata carries it.

    Returns the directory to mount, or ``None`` when there is nothing to
    pass through (the common case: credentials come from the auth volume).
    """
    present = [var for var in _PASS_THROUGH_ENV if os.environ.get(var)]
    if not present:
        return None
    prune_stale_secret_dirs()
    directory = _secret_dir_root() / (container_name or 'unknown')
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    for var in present:
        target = directory / var
        # Create with 0600 from the start — writing then chmod'ing leaves
        # a window where the secret is world-readable.
        handle = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(os.environ[var])
    return directory


def prune_stale_secret_dirs() -> list[str]:
    """Delete per-spawn secret drops whose container is no longer running.

    The drop has to outlive ``docker run``'s start (the entrypoint reads
    it), so it cannot be deleted inline. Pruning on the next spawn and at
    boot keeps the set bounded to live containers.
    """
    root = _secret_dir_root()
    if not root.is_dir():
        return []
    try:
        listed = subprocess.run(
            ['docker', 'ps', '--format', '{{.Names}}'],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=30,
        )
        running = set(listed.stdout.split()) if listed.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        running = None
    if running is None:
        # Docker unreachable: leave everything alone rather than delete a
        # live container's secrets out from under it.
        return []
    removed: list[str] = []
    for child in root.iterdir():
        if not child.is_dir() or child.name in running:
            continue
        shutil.rmtree(child, ignore_errors=True)
        removed.append(child.name)
    return removed


def host_boot_identity() -> str:
    """A string that changes when the host reboots ('' if unknowable).

    Used to disambiguate the one case a PID check gets wrong: after a
    reboot, PIDs are reused from scratch, so a dead controller's PID
    can easily be alive again as something unrelated — and the stale
    container would be spared forever. A boot identity that differs
    from the label's is proof the owner is gone.
    """
    if sys.platform == 'win32':                  # pragma: no cover - platform
        # No /proc and no sysctl. Boot time = now - uptime; GetTickCount64
        # returns milliseconds since boot and is monotonic across the
        # session, so the derived value is stable within a boot and
        # changes across one — exactly what the reaper needs.
        try:
            import ctypes
            uptime_ms = ctypes.windll.kernel32.GetTickCount64()
            return str(int(time.time() - (uptime_ms / 1000.0)))
        except Exception:
            return ''
    linux_boot_id = Path('/proc/sys/kernel/random/boot_id')
    try:
        if linux_boot_id.is_file():
            return linux_boot_id.read_text(encoding='utf-8').strip()
    except OSError:
        return ''
    try:
        result = subprocess.run(
            ['sysctl', '-n', 'kern.boottime'],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ''
    if result.returncode != 0:
        return ''
    # macOS/BSD print e.g. ``{ sec = 1755..., usec = 1 } Sun Aug 17 ...``.
    # The seconds field alone is stable and reboot-unique.
    text = result.stdout.strip()
    for chunk in text.replace(',', ' ').split():
        if chunk.isdigit() and len(chunk) >= 9:
            return chunk
    return text[:64]


def _process_is_alive(pid: int) -> bool:
    """True when ``pid`` exists. EPERM counts as alive (another user).

    WINDOWS MATTERS HERE. ``os.kill(pid, 0)`` is the POSIX idiom for
    "does this process exist", but on Windows Python maps any signal
    other than CTRL_C_EVENT/CTRL_BREAK_EVENT onto ``TerminateProcess``
    — so the POSIX spelling would KILL the very process it is asking
    about. The reaper calls this for every labelled container's owner,
    which on Windows means it would kill live owner processes.
    """
    if pid <= 0:
        return False
    if sys.platform == 'win32':                  # pragma: no cover - platform
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid,
        )
        if not handle:
            # ACCESS_DENIED means it exists but belongs to someone else;
            # anything else (typically INVALID_PARAMETER) means it is gone.
            ERROR_ACCESS_DENIED = 5
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True                      # exists, cannot read status
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def reap_orphan_sandbox_containers(
    *,
    logger: logging.Logger | None = None,
) -> list[str]:
    """Force-remove sandbox containers whose controlling process is gone.

    ``docker run --rm`` cleans up when the CONTAINER's process exits —
    it does nothing when the process that launched it dies instead. A
    hard crash (SIGKILL, power loss, a panicked terminal) therefore
    leaves a container running with the task workspace bind-mounted and
    credentials in its tmpfs, for as long as the machine stays up.

    Called at boot, before any new sandbox is spawned. A container is
    reaped when its recorded boot identity differs from the current one
    (host rebooted — the owner cannot possibly be alive) or its owner
    PID is no longer running. Containers owned by a LIVE process are
    left strictly alone, so a second concurrent instance on the same
    host never has its sandboxes pulled out from under it.

    Returns the container ids removed. Never raises: a reap failure
    must not block startup.
    """
    separator = '\t'
    fmt = separator.join((
        '{{.ID}}',
        f'{{{{.Label "{_OWNER_PID_LABEL}"}}}}',
        f'{{{{.Label "{_OWNER_BOOT_LABEL}"}}}}',
    ))
    try:
        listed = subprocess.run(
            [
                'docker', 'ps', '--no-trunc',
                '--filter', f'label={_IMAGE_IDENTITY_LABEL}={_IMAGE_IDENTITY_VALUE}',
                '--format', fmt,
            ],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if logger is not None:
            logger.warning('sandbox orphan sweep could not list containers: %s', exc)
        return []
    if listed.returncode != 0:
        if logger is not None:
            logger.warning(
                'sandbox orphan sweep could not list containers: rc=%s %s',
                listed.returncode, (listed.stderr or '').strip()[:200],
            )
        return []

    current_boot = host_boot_identity()
    removed: list[str] = []
    for line in listed.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(separator)
        container_id = parts[0].strip()
        owner_pid = parts[1].strip() if len(parts) > 1 else ''
        owner_boot = parts[2].strip() if len(parts) > 2 else ''
        if not container_id:
            continue
        if not _orphaned(owner_pid, owner_boot, current_boot):
            continue
        try:
            removal = subprocess.run(
                ['docker', 'rm', '--force', container_id],
                capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            if logger is not None:
                logger.warning(
                    'failed to reap orphan sandbox %s: %s', container_id[:12], exc,
                )
            continue
        if removal.returncode != 0:
            if logger is not None:
                logger.warning(
                    'failed to reap orphan sandbox %s: %s',
                    container_id[:12], (removal.stderr or '').strip()[:200],
                )
            continue
        removed.append(container_id)
        if logger is not None:
            logger.warning(
                'reaped orphaned sandbox container %s (owner pid %s, boot %s) — '
                'its controlling process is gone',
                container_id[:12], owner_pid or 'unknown', owner_boot or 'unknown',
            )
    return removed


def _orphaned(owner_pid: str, owner_boot: str, current_boot: str) -> bool:
    """Whether a labelled container's owner is provably gone.

    Unlabelled containers (older builds, or something else wearing the
    sandbox label) are treated as orphans ONLY when the PID label is
    absent entirely — there is no owner to protect in that case, and
    leaving them running is the very leak this sweep exists to close.
    """
    if owner_boot and current_boot and owner_boot != current_boot:
        return True
    if not owner_pid:
        return True
    try:
        pid = int(owner_pid)
    except ValueError:
        return True
    return not _process_is_alive(pid)


def flag_present_in_argv(argv: list[str], flag: str) -> bool:
    """True if ``flag`` is in ``argv``, in either form Docker accepts.

    * ``--key=value`` as a single token, or
    * ``--key value`` as two adjacent tokens.

    Boolean flags (``--read-only``) match verbatim. Lives here rather
    than in the drift-guard test because the SPAWN path now enforces
    the same sets at runtime — one matcher, one meaning.
    """
    if '=' not in flag:
        return flag in argv
    if flag in argv:
        return True
    key, value = flag.split('=', 1)
    for i, token in enumerate(argv):
        if token == key and i + 1 < len(argv) and argv[i + 1] == value:
            return True
    return False


def _assert_isolated_network(argv: list[str]) -> None:
    """The sandbox must join an isolated network, never a shared default.

    This replaced a literal ``--network=<fixed name>`` requirement. The
    name is no longer fixed — each task now gets its own two-member
    network — but the PROPERTY still has to hold, and it is the property
    the threat model depends on: never the host stack, never Docker's
    default bridge (where every unrelated container on the machine is a
    neighbour), never ``none``.
    """
    values = [
        argv[index + 1] for index, token in enumerate(argv)
        if token == '--network' and index + 1 < len(argv)
    ]
    values += [
        token.split('=', 1)[1] for token in argv if token.startswith('--network=')
    ]
    if len(values) != 1:
        raise SandboxError(
            f'sandbox argv must join exactly one network, found {len(values)}',
        )
    network = values[0]
    if network in ('host', 'bridge', 'none', 'default', ''):
        raise SandboxError(
            f'sandbox must not use the {network!r} network — it needs an '
            f'isolated bridge, not the host stack or the shared default.',
        )


def _assert_sandbox_flags(argv: list[str]) -> None:
    """Enforce the required/forbidden flag sets against the REAL argv.

    The drift-guard test already asserts these sets against a
    representative argv — but that proves the code in CI, not the
    command about to run on THIS machine. Anything that builds argv by
    another path (a future caller, an env-driven branch, a bad merge)
    would ship a downgraded sandbox with every test still green. This
    check runs microseconds before ``Popen`` and fails closed.
    """
    missing = sorted(
        flag for flag in _REQUIRED_DOCKER_FLAGS
        if not flag_present_in_argv(argv, flag)
    )
    forbidden = sorted(
        flag for flag in _FORBIDDEN_DOCKER_FLAGS
        if flag_present_in_argv(argv, flag)
    )
    if not missing and not forbidden:
        return
    problems = []
    if missing:
        problems.append(f'missing required flag(s): {", ".join(missing)}')
    if forbidden:
        problems.append(f'forbidden flag(s) present: {", ".join(forbidden)}')
    raise SandboxError(
        'refusing to spawn a sandbox that does not match the declared '
        'security invariants — ' + '; '.join(problems) + '. Fix the argv '
        'builder; do NOT relax the invariant sets in manager.py without '
        'updating SANDBOX_PROTECTIONS.md and re-reading the threat model.',
    )


def _assert_seccomp_pinned(argv: list[str]) -> None:
    """Require exactly one seccomp option, pinned to the vendored profile.

    ``_assert_seccomp_not_unconfined`` only catches an explicit
    downgrade; a spawn with NO seccomp option at all used to pass it
    while silently inheriting whatever the daemon calls "default" —
    which the host controls via ``dockerd --seccomp-profile``. This
    asserts the positive property instead.
    """
    values = [
        argv[i + 1] for i, token in enumerate(argv)
        if token == '--security-opt' and i + 1 < len(argv)
        and argv[i + 1].startswith('seccomp=')
    ]
    values += [
        token.split('=', 1)[1] for token in argv
        if token.startswith('--security-opt=seccomp=')
    ]
    if len(values) != 1:
        raise SandboxError(
            f'sandbox argv must carry exactly one seccomp option, found '
            f'{len(values)} — refusing to spawn.',
        )
    pinned = values[0].split('=', 1)[1]
    if pinned != str(_SECCOMP_PROFILE_PATH):
        raise SandboxError(
            f'sandbox seccomp profile must be the vendored '
            f'{_SECCOMP_PROFILE_PATH}, got {pinned!r} — refusing to spawn.',
        )
    if not _SECCOMP_PROFILE_PATH.is_file():
        raise SandboxError(
            f'vendored seccomp profile is missing at '
            f'{_SECCOMP_PROFILE_PATH} — refusing to spawn without an '
            'explicitly pinned syscall policy.',
        )


def _pinned_image_reference(image_tag: str) -> str:
    """The image's resolved ID, or the tag if it cannot be resolved.

    Referencing the mutable tag lets anything that can retag it choose
    what runs; the spawn path has always pinned, and the login path had
    not. Falls back to the tag rather than refusing, because a failure
    here would block the operator's only way to seed credentials.
    """
    try:
        digest = _image_digest_strict(image_tag)
    except _DigestLookupError:
        return image_tag
    return digest if digest.startswith('sha256:') else f'sha256:{digest.split(":")[-1]}'


def login_command(image_tag: str = SANDBOX_IMAGE_TAG) -> list[str]:
    """One-time interactive ``claude /login`` invocation for the sandbox.

    Run this from a normal terminal (``-it``, not piped) to seed the
    persistent auth volume with the operator's credentials. After
    this, kato-spawned sandbox containers reuse the same volume —
    but only via a **read-only** source mount; the credentials are
    copied (allowlisted basenames only) into a per-task tmpfs at
    spawn time, so this login flow is the *only* path that writes
    the persistent volume.

    Uses the same hardening as ``wrap_command`` minus the workspace
    mount (login doesn't touch task files). The auth volume is
    mounted **read-write** here (and only here) so the operator's
    typed credentials persist across containers.
    """
    argv = [
        'docker', 'run',
        '--rm',
        '-it',
        '--init',
        '--label', 'org.kato.sandbox=true',
        '--label', 'org.kato.task-id=login',
        '--label', f'org.kato.auth-volume={_AUTH_VOLUME_NAME}',
        '--network', _SANDBOX_NETWORK_NAME,
        '--ipc=none',
        '--cgroupns=private',
        '--cap-drop', 'ALL',
        '--cap-add', 'NET_ADMIN',
        '--cap-add', 'NET_RAW',
        '--cap-add', 'SETUID',
        '--cap-add', 'SETGID',
        # Same reason as the spawn path: the shared entrypoint chowns
        # the config dir to the claude user and now fails loudly if it
        # cannot.
        '--cap-add', 'CHOWN',
        '--cap-add', 'SETPCAP',
        '--security-opt', 'no-new-privileges',
        '--security-opt', 'apparmor=docker-default',
        # Parity with the spawn path. This container types the operator's
        # real credentials and writes them to the persistent volume, so
        # "the login container is only used briefly" is a reason to harden
        # it, not to skip it: the pinned profile was missing here while
        # every spawn had it.
        '--security-opt', f'seccomp={_SECCOMP_PROFILE_PATH}',
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
        # Login mode: auth volume RW directly at .claude (no
        # /auth-src mount, no tmpfs). Entrypoint detects the absence
        # of /auth-src and skips the copy-in step.
        '-v', f'{_AUTH_VOLUME_NAME}:{_CLAUDE_HOME}/.claude:rw',
        _pinned_image_reference(image_tag),
        'claude', '/login',
    ]
    # Same kernel boundary as a spawn. gVisor is mandatory for spawns; the
    # login container handles the credentials those spawns will use, so it
    # gets the runtime too whenever it is available.
    if gvisor_runtime_available():
        argv[2:2] = ['--runtime', 'runsc']
    return argv


def stamp_auth_volume_manifest(
    image_tag: str = SANDBOX_IMAGE_TAG,
    *,
    logger: logging.Logger | None = None,
) -> None:
    """Refresh the integrity manifest stored inside the auth volume.

    Call this immediately after a successful ``claude /login``: it
    spins up a one-shot root container, writes
    ``manifest.sha256`` containing ``sha256(.credentials.json)``
    (and any other allowlisted credential file present), then exits.

    Subsequent **spawn-mode** containers verify this manifest in
    ``entrypoint.sh`` and refuse to start if a credential file's
    hash doesn't match — i.e. someone tampered with the volume out
    of band (manual ``docker volume`` edit, sibling container with
    the same volume mounted RW, etc.). Login mode skips the check
    because it is the legitimate path that mutates the volume.

    Idempotent and best-effort: a manifest-write failure is logged
    at warning level but never aborts. Operators can re-run
    ``make sandbox-login`` to refresh the manifest at any time.
    """
    cmd = [
        'docker', 'run',
        '--rm',
        '--init',
        '--network', 'none',           # manifest writer needs no egress
        '--ipc=none',
        '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges',
        '--read-only',
        '--tmpfs', '/tmp:rw,nosuid,nodev,size=8m',
        '--memory', '128m',
        '--memory-swap', '128m',
        '--pids-limit', '32',
        '-v', f'{_AUTH_VOLUME_NAME}:/auth:rw',
        '--entrypoint', '/bin/bash',
        image_tag,
        '-c',
        # Two-line script: list allowlisted basenames present, then
        # write a fresh manifest. The shell is exec'd as root inside
        # the container (we explicitly DROP all caps and forbid
        # privilege escalation, so root here can do approximately
        # nothing except write to /auth, which is the point).
        'cd /auth && '
        'rm -f manifest.sha256 && '
        'for f in .credentials.json credentials.json; do '
        '  [ -f "$f" ] && sha256sum "$f" >> manifest.sha256; '
        'done; '
        '[ -f manifest.sha256 ] && chmod 600 manifest.sha256 || true',
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if logger is not None:
            logger.warning(
                'failed to stamp auth volume manifest (%s); '
                'subsequent spawns will skip integrity check',
                exc,
            )
        return
    if result.returncode != 0 and logger is not None:
        logger.warning(
            'auth volume manifest write returned non-zero: %s',
            result.stderr.strip() or '(no stderr)',
        )
