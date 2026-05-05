"""Hard-stop safety gate for ``KATO_CLAUDE_DOCKER`` / ``KATO_CLAUDE_BYPASS_PERMISSIONS``.

Two independent flags with one constraint between them:

  * ``KATO_CLAUDE_DOCKER=true``  — wraps every Claude spawn in the
    hardened Docker sandbox. Containment layers (workspace validator,
    egress firewall, capability drop, read-only rootfs, audit log,
    gVisor on Linux) gate on this flag.
  * ``KATO_CLAUDE_BYPASS_PERMISSIONS=true`` — disables per-tool
    permission prompts. The agent runs every tool without asking.
  * **Constraint:** bypass requires docker. Bypass without docker is
    refused at startup (fail fast, not at first sandbox spawn).

Three valid modes, one refused mode, plus the all-off default:

  * docker=false, bypass=false → host execution, prompts on (default)
  * docker=true,  bypass=false → sandboxed, prompts on (NEW; recommended)
  * docker=true,  bypass=true  → sandboxed, prompts off (the original "bypass mode")
  * docker=false, bypass=true  → REFUSED at startup

Decisions (in order):

1. **Both flags off** -> nothing to do (host execution, prompts on).
2. **Bypass on** -> emit the bypass red banner to stderr.
3. **Bypass on AND docker off** -> refuse. Bypass disables the
   per-tool prompts (the social layer); without docker there is no
   sandbox (the structural layer) either, so the agent has neither
   bound. Refuse and tell the operator to set ``KATO_CLAUDE_DOCKER=true``.
4. **Docker on, native Windows Python (sys.platform == 'win32')** ->
   refuse. The sandbox image is Linux, the path validator assumes
   POSIX semantics, ``fcntl.flock`` for the audit chain is unavailable,
   and ``os.geteuid`` is absent. Operators on Windows should run kato
   from inside a WSL2 distribution; Docker Desktop's WSL2 backend will
   spawn the sandbox correctly. Refusal mentions docker explicitly so
   a host-mode user who turns docker on doesn't think kato itself is
   broken.
5. **(Bypass OR docker), running as root** -> refuse. Root + autonomous
   coding agent is the worst possible blast radius — true under either
   flag (root inside a sandboxed container is also bad even with caps
   dropped). There is no legitimate reason to combine euid 0 with kato
   running an agent.
6. **Bypass on, non-interactive (CI/Docker/cron, no TTY)** -> refuse.
   With no second-factor env var, there is no way to acknowledge from
   a non-interactive runner. Docker-only mode allows non-TTY runs;
   only bypass requires the TTY confirmation.
7. **Bypass on, interactive TTY** -> prompt the operator twice with
   ``prompt_yes_no``. Both must answer yes; either no -> refuse.

The stderr banner is written *before* logger configuration runs so log
level cannot suppress it. SECURITY.md and BYPASS_PROTECTIONS.md are
the canonical references; the latter has the per-mode capability
table and the layer-vs-flag classification.
"""

from __future__ import annotations

import os
import sys

try:
    # Use ``prompt_yes_no`` (not ``input_yes_no``) — it loops on
    # invalid input instead of silently defaulting, which matters for
    # a security prompt: a fat-fingered Enter must not accept "yes".
    from core_lib.helpers.command_line import prompt_yes_no as _core_prompt_yes_no
except (ImportError, ModuleNotFoundError):  # pragma: no cover - core-lib is required
    _core_prompt_yes_no = None


BYPASS_ENV_KEY = 'KATO_CLAUDE_BYPASS_PERMISSIONS'
DOCKER_ENV_KEY = 'KATO_CLAUDE_DOCKER'
READ_ONLY_TOOLS_ENV_KEY = 'KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS'
TRUE_VALUES = frozenset({'1', 'true', 'yes', 'on'})


# Hardcoded read-only Bash allowlist for the
# ``KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS=true`` opt-in.
#
# Each entry is a Claude Code permission pattern. The shape
# ``Bash(<cmd>:*)`` matches "any args to <cmd>". We restrict to
# commands that read state (file contents, directory listings,
# metadata) and never write — so pre-approving them inside the
# sandbox can't mutate the workspace, the host (the workspace is
# the only writable mount), or anything outside ``api.anthropic.com``
# (the egress firewall blocks the rest).
#
# Why this is hardcoded, not operator-extensible:
#   * Adding a tool here is a security decision: an operator who
#     widens via env var can be wrong about what's read-only
#     (``find -delete`` is not, ``sed -i`` is not, ``tee`` is not).
#   * Code-level edits force a security review; an env-var-extensible
#     allowlist degrades silently if the operator's mental model of
#     "read-only" is wrong.
#   * The sandbox is the structural backstop, but the allowlist is the
#     narrowest of the protections — keeping it tight means a future
#     sandbox bug (e.g. write-mount escape) doesn't multiply.
#
# This list is locked by a drift-guard test in
# ``tests/test_open_gap_closures_doc_consistency.py`` (or a sibling
# pin test) — widening it requires changing both the constant and
# the test, which forces the change through review.
READ_ONLY_TOOLS_ALLOWLIST = frozenset({
    'Bash(grep:*)',
    'Bash(rg:*)',
    'Bash(ls:*)',
    'Bash(cat:*)',
    'Bash(find:*)',
    'Bash(head:*)',
    'Bash(tail:*)',
    'Bash(wc:*)',
    'Bash(file:*)',
    'Bash(stat:*)',
    'Read',
})

_BANNER_LINE = '!' * 78
_BANNER = (
    '\n'
    f'{_BANNER_LINE}\n'
    '!! KATO_CLAUDE_BYPASS_PERMISSIONS=true\n'
    '!! Claude will run with --permission-mode bypassPermissions.\n'
    '!! Per-tool prompts are disabled. The agent can run Bash, Edit,\n'
    '!! Write, and any other tool without asking.\n'
    '!! The operator who set this flag accepts responsibility for any\n'
    '!! harm caused by the agent. See SECURITY.md.\n'
    f'{_BANNER_LINE}\n'
)


class BypassPermissionsRefused(RuntimeError):
    """Raised when the safety gate refuses to allow the process to continue."""


def is_bypass_enabled(env: dict | None = None) -> bool:
    source = env if env is not None else os.environ
    return str(source.get(BYPASS_ENV_KEY, '')).strip().lower() in TRUE_VALUES


def is_read_only_tools_enabled(env: dict | None = None) -> bool:
    """True when ``KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS=true``.

    Pre-approves the hardcoded ``READ_ONLY_TOOLS_ALLOWLIST`` so the
    operator isn't prompted for those specific Bash invocations.
    Independent of ``KATO_CLAUDE_BYPASS_PERMISSIONS`` (this disables
    only the read-only prompts; bypass disables ALL prompts).
    Constrained: requires ``KATO_CLAUDE_DOCKER=true``. Without the
    sandbox, even ``grep`` runs on the host and can read SSH keys
    or any file the operator can read; the structural boundary is
    the prerequisite for letting prompts be skipped at all.
    Refused at startup; see ``validate_read_only_tools_requires_docker``.
    """
    source = env if env is not None else os.environ
    return str(source.get(READ_ONLY_TOOLS_ENV_KEY, '')).strip().lower() in TRUE_VALUES


def is_docker_mode_enabled(env: dict | None = None) -> bool:
    """True when KATO_CLAUDE_DOCKER=true.

    Docker mode wraps every Claude spawn in the hardened sandbox
    (workspace bind-mount only, default-DROP egress firewall,
    capability drop, read-only rootfs, audit log). Independent of
    bypass mode — the operator can have docker without bypass
    (per-tool prompts via planning UI continue to work) or both
    together (the original "bypass mode"). Bypass without docker
    is refused at startup; see ``validate_bypass_permissions``.
    """
    source = env if env is not None else os.environ
    return str(source.get(DOCKER_ENV_KEY, '')).strip().lower() in TRUE_VALUES


def is_running_as_root() -> bool:
    """POSIX root check. Windows has no euid; treat as not-root there."""
    geteuid = getattr(os, 'geteuid', None)
    if geteuid is None:
        return False
    return geteuid() == 0


def _is_interactive_stdin(stream=None) -> bool:
    candidate = stream if stream is not None else sys.stdin
    isatty = getattr(candidate, 'isatty', None)
    if isatty is None:
        return False
    try:
        return bool(isatty())
    except (ValueError, OSError):
        return False


def _emit_banner(stderr=None) -> None:
    target = stderr if stderr is not None else sys.stderr
    try:
        target.write(_BANNER)
        target.flush()
    except (ValueError, OSError):
        # stderr can be closed in odd embedded scenarios; never crash on it.
        pass


# Prompts shown to the operator in interactive mode. Two of them so a
# fat-fingered Enter doesn't sail through the "are you sure" question.
# The wording escalates: Q1 frames what's happening, Q2 makes the
# operator commit to "yes, I really mean this for the whole session".
_PROMPT_FIRST = (
    f'{BYPASS_ENV_KEY}=true. The agent will run every tool '
    'without asking, including Bash, Edit, and Write. Are you sure '
    'you want to continue?'
)
_PROMPT_SECOND = (
    'Final confirmation: this disables every per-tool permission '
    'prompt for the *entire* kato session, not just this turn. '
    'Continue?'
)


def validate_bypass_permissions(
    *,
    env: dict | None = None,
    stderr=None,
    stdin=None,
    yes_no_prompter=None,
) -> None:
    """Run the safety gate. Returns silently when allowed; raises on refusal.

    Parameters mirror the unit-test seams: pass an env dict, a fake stderr,
    a fake stdin, and a fake prompter to exercise each branch without
    touching the real process state.

    Decision order (matches the module docstring):

      1. Both flags off -> return silently.
      2. Bypass on -> emit the bypass red banner.
      3. Bypass on AND docker off -> refuse (bypass requires docker).
      4. Docker on, native Windows -> refuse (sandbox image is Linux).
      5. Bypass OR docker, running as root -> refuse.
      6. Bypass on, no TTY -> refuse.
      7. Bypass on, TTY -> double prompt; either no -> refuse.
    """
    bypass_on = is_bypass_enabled(env)
    docker_on = is_docker_mode_enabled(env)

    if not bypass_on and not docker_on:
        # Both flags off: host execution, prompts on. Nothing to gate.
        return

    if bypass_on:
        _emit_banner(stderr=stderr)

    # Bypass without docker is the one refused combination. Bypass
    # disables the per-tool prompts (the social layer that catches
    # unexpected actions when the operator clicks no); without docker
    # there is no sandbox (the structural layer that catches them by
    # making host paths outside /workspace unreachable). Bypass turns
    # off the social layer; without docker there is no structural
    # layer either. Refuse and tell the operator the simplest fix.
    if bypass_on and not docker_on:
        raise BypassPermissionsRefused(
            f'{BYPASS_ENV_KEY}=true requires {DOCKER_ENV_KEY}=true.\n'
            '\n'
            'Bypass mode disables every per-tool permission prompt. '
            'Without the Docker sandbox, the agent runs on your host, '
            'with your file-system access, your network access, and '
            'your credentials — and nothing asks before it runs Bash, '
            'Edit, Write, or any other tool.\n'
            '\n'
            'The two protections work as a pair:\n'
            '  - Per-tool prompts catch unexpected actions SOCIALLY '
            '(you see the prompt, you click no).\n'
            '  - The Docker sandbox catches them STRUCTURALLY (files '
            'outside the per-task workspace are not reachable '
            'regardless of how many prompts you misclick).\n'
            '\n'
            'Bypass turns off the social layer. Without docker there '
            'is no structural layer either. Kato refuses that '
            'combination at startup.\n'
            '\n'
            'Pick one:\n'
            '  1. Add the structural layer (this gives you the '
            f'strongest protection): export {DOCKER_ENV_KEY}=true. '
            'Claude will run inside the hardened sandbox. All '
            'existing bypass-mode protections apply.\n'
            f'  2. Restore the social layer: unset {BYPASS_ENV_KEY}. '
            'Claude will run with per-tool permission prompts via '
            'the planning UI.\n'
            '\n'
            'See BYPASS_PROTECTIONS.md for the full threat model.'
        )

    # Docker mode is incompatible with native Windows Python regardless
    # of bypass. The sandbox image is Linux (node:22-bookworm-slim),
    # the workspace path validation assumes POSIX semantics
    # (forbidden-mount lists, Path.home() resolution), ``fcntl.flock``
    # for the audit chain is unavailable, and ``os.geteuid`` is absent
    # so the root refusal below would silently no-op. Naming docker
    # explicitly in the message means a host-mode user who turns
    # docker on doesn't think kato itself is broken — the message
    # tells them which flag is incompatible.
    if docker_on and sys.platform == 'win32':
        raise BypassPermissionsRefused(
            f'{DOCKER_ENV_KEY}=true is not supported on native Windows. '
            'The sandbox image is Linux-based and the workspace path '
            'validation assumes POSIX semantics. Run kato from inside '
            'a WSL2 distribution (Ubuntu, Debian) — Docker Desktop\'s '
            'WSL2 backend will spawn the sandbox correctly. See '
            'BYPASS_PROTECTIONS.md for the cross-OS support matrix. '
            f'(If you do not need the sandbox, unset {DOCKER_ENV_KEY} '
            'and kato will run Claude on the host with per-tool prompts.)'
        )

    # Root + autonomous coding agent is the worst possible blast radius
    # in either mode. Root + bypass is catastrophic (no prompts AND
    # full host privilege); root + docker is also bad (root inside a
    # sandboxed container is a stronger attack surface even with caps
    # dropped — the bounding-set wipe still applies, but defense in
    # depth says don't combine root with the agent). Apply on either
    # flag; the named flag in the error message is whichever is on.
    if (bypass_on or docker_on) and is_running_as_root():
        named_flag = BYPASS_ENV_KEY if bypass_on else DOCKER_ENV_KEY
        raise BypassPermissionsRefused(
            f'{named_flag}=true is not allowed when running as root. '
            'Run kato as an unprivileged user. See SECURITY.md.'
        )

    if not bypass_on:
        # Docker-only mode (sandbox + per-tool prompts via planning UI):
        # no double-prompt, non-TTY runs are allowed (CI / cron / etc.
        # can use docker-only mode safely because the sandbox bounds
        # blast radius without operator confirmation needed).
        return

    if not _is_interactive_stdin(stream=stdin):
        raise BypassPermissionsRefused(
            f'{BYPASS_ENV_KEY}=true requires interactive confirmation at '
            'startup, but stdin is not a TTY (CI / Docker / cron / '
            'systemd). Either run kato interactively so the operator '
            f'can confirm at the terminal, or unset {BYPASS_ENV_KEY} '
            f'(setting only {DOCKER_ENV_KEY}=true gives you the '
            'sandbox without the per-tool-prompt removal, and works '
            'on non-interactive runners).'
        )

    prompter = yes_no_prompter if yes_no_prompter is not None else _core_prompt_yes_no
    if prompter is None:  # pragma: no cover - core-lib is required
        raise BypassPermissionsRefused(
            'core-lib prompt_yes_no helper is unavailable; cannot prompt '
            f'for confirmation. Unset {BYPASS_ENV_KEY} to start kato.'
        )

    # Two-step confirmation. Both must be yes; either no -> refuse.
    # ``prompt_yes_no`` loops on invalid input until y/n is given, so
    # a stray Enter does not select the default.
    if not bool(prompter(_PROMPT_FIRST, False)):
        raise BypassPermissionsRefused(
            'Operator declined the bypass-permissions prompt. Aborting.'
        )
    if not bool(prompter(_PROMPT_SECOND, False)):
        raise BypassPermissionsRefused(
            'Operator declined the bypass-permissions final confirmation. '
            'Aborting.'
        )


def validate_read_only_tools_requires_docker(*, env: dict | None = None) -> None:
    """Refuse if ``KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS=true`` lacks docker.

    Same constraint pattern as ``validate_bypass_permissions``'s
    "bypass requires docker" gate: pre-approving any tool — even a
    read-only one — only makes sense inside the sandbox boundary.
    Without docker, ``grep ~/.ssh/id_rsa`` runs on the host and
    succeeds. The sandbox bind-mounts only the per-task workspace
    and drops capabilities, so the same ``grep`` reaches nothing
    sensitive.

    Decision order:

      1. Read-only flag off -> return silently.
      2. Read-only flag on AND docker on -> return silently (the
         valid combination).
      3. Read-only flag on AND docker off -> refuse with a message
         that names the fix (``export KATO_CLAUDE_DOCKER=true``).
    """
    if not is_read_only_tools_enabled(env):
        return
    if is_docker_mode_enabled(env):
        return
    raise BypassPermissionsRefused(
        f'{READ_ONLY_TOOLS_ENV_KEY}=true requires {DOCKER_ENV_KEY}=true.\n'
        '\n'
        f'{READ_ONLY_TOOLS_ENV_KEY} pre-approves a hardcoded list of '
        'read-only Bash commands (grep, rg, ls, cat, find, head, tail, '
        'wc, file, stat) plus the Read tool, so the operator is not '
        'prompted for them. "Read-only" only means safe inside the '
        'sandbox boundary:\n'
        '\n'
        '  - Without docker, grep / cat / find run on the host with '
        'your file-system access. ``grep -r AWS_SECRET ~`` or '
        '``cat ~/.ssh/id_rsa`` would succeed without prompting you.\n'
        '  - With docker, the same commands run inside the sandbox '
        'where only the per-task workspace is bind-mounted; everything '
        'outside is structurally unreachable.\n'
        '\n'
        'The sandbox is the prerequisite for letting any prompt be '
        'skipped, even for read-only tools.\n'
        '\n'
        'Pick one:\n'
        f'  1. Add the sandbox: export {DOCKER_ENV_KEY}=true. '
        'Read-only pre-approval activates and the structural boundary '
        'bounds what the pre-approved tools can reach.\n'
        f'  2. Drop the pre-approval: unset {READ_ONLY_TOOLS_ENV_KEY}. '
        'Per-tool prompts continue to fire for every tool, including '
        'the read-only ones.\n'
        '\n'
        'See BYPASS_PROTECTIONS.md for the full mode matrix.'
    )


_SAFE_DEFAULT_ALLOWED_TOOLS = frozenset({'Edit', 'Write', 'Read', 'Bash', 'Glob', 'Grep'})


def print_security_posture(*, env: dict | None = None, stderr=None) -> None:
    """Always-visible boot banner summarizing the security posture.

    Bypassing log level: writes to stderr directly. The intent is that an
    operator scanning the kato boot output sees, at one glance, every
    flag that affects what the agent can do.

    Three valid mode shapes, three banner variants:

      * docker=false, bypass=false → Mode 1 (default). Adds a soft hint
        suggesting ``KATO_CLAUDE_DOCKER=true`` for stronger isolation.
      * docker=true,  bypass=false → Mode 2 (NEW: belt+suspenders).
        Lists active containment layers and notes the bypass path
        symmetrically so operators discover both flags from either side.
      * docker=true,  bypass=true  → Mode 3 (full bypass; original
        behavior). Adds an explicit "containment layers remain active"
        line so the operator knows bypass doesn't disable the sandbox.
    """
    source = env if env is not None else os.environ
    target = stderr if stderr is not None else sys.stderr

    bypass = is_bypass_enabled(source)
    docker = is_docker_mode_enabled(source)
    read_only = is_read_only_tools_enabled(source)
    running_root = is_running_as_root()
    allowed_tools = str(source.get('KATO_CLAUDE_ALLOWED_TOOLS', '')).strip()
    arch_doc = str(source.get('KATO_ARCHITECTURE_DOC_PATH', '')).strip()
    backend = str(source.get('KATO_AGENT_BACKEND', 'openhands')).strip()

    extra_tools: list[str] = []
    if allowed_tools:
        for entry in allowed_tools.split(','):
            normalized = entry.strip()
            if normalized and normalized not in _SAFE_DEFAULT_ALLOWED_TOOLS:
                extra_tools.append(normalized)

    bar = '=' * 78
    # Three flags now means more banner lines. Show ``read-only
    # pre-approval`` on its own row so the operator sees their actual
    # posture at a glance — bypass-off + read-only-on means SOME
    # prompts are skipped (the read-only ones) while others still
    # fire. That distinction is invisible from the bypass row alone.
    read_only_label = 'true' if read_only else 'false'
    if read_only and not bypass:
        # Read-only without bypass: prompts skipped only for the
        # hardcoded read-only allowlist. Worth surfacing because
        # the operator's mental model from "bypass off = every
        # tool prompts" no longer holds.
        read_only_suffix = '   ⚠ grep/cat/ls/find/Read no longer prompt'
    elif read_only and bypass:
        # Bypass-on already disables every prompt; the read-only
        # flag is redundant here. Mark it as such so the operator
        # knows it's not buying anything in this mode.
        read_only_suffix = '   (redundant: bypass disables ALL prompts)'
    else:
        read_only_suffix = ''
    header = [
        '',
        bar,
        ' kato — security posture at boot',
        bar,
        f'  agent backend         : {backend}',
        f'  docker sandbox        : {"true" if docker else "false"}',
        f'  bypass permissions    : {"true" if bypass else "false"}'
        + ('   ⚠ per-tool prompts OFF' if bypass else ''),
        f'  read-only pre-approval: {read_only_label}{read_only_suffix}',
        f'  running as root       : {"yes" if running_root else "no"}',
        f'  architecture doc      : {arch_doc or "(not set)"}',
        f'  allowed-tools (extra) : {", ".join(extra_tools) if extra_tools else "(safe default only)"}',
        f'  git operations by Claude: BLOCKED (Bash(git:*) on every spawn)',
    ]

    mode_lines: list[str] = ['']
    if not docker and not bypass:
        # Mode 1 — default. Soft hint nudging operators toward docker mode.
        mode_lines.extend([
            '  ℹ For stronger isolation, consider:',
            '      export KATO_CLAUDE_DOCKER=true',
            '    Claude will run inside the hardened sandbox (workspace',
            '    bind-mount only, default-DROP egress firewall, capability',
            '    drop). Per-tool prompts via the planning UI continue to',
            '    work as today.',
        ])
    elif docker and not bypass:
        # Mode 2 — NEW belt+suspenders. List containment layers + the
        # symmetric hint about bypass so operators discover both flags
        # from either side without being pushed toward bypass.
        mode_lines.extend([
            '  Active containment layers:',
            '    • Workspace bind-mount only (host paths outside /workspace',
            '      are structurally unreachable)',
            '    • Default-DROP egress firewall (api.anthropic.com:443 only)',
            '    • Capability drop ALL + non-root container user',
            '    • Read-only rootfs + per-task tmpfs for /home/claude/.claude',
            '    • gVisor runtime (or KATO_SANDBOX_ALLOW_NO_GVISOR=true override)',
            '    • Audit log with hash chain at ~/.kato/sandbox-audit.log',
            '',
            '  See BYPASS_PROTECTIONS.md for the full layer breakdown.',
            '',
            '  If you would rather skip the per-tool prompts, set',
            '  KATO_CLAUDE_BYPASS_PERMISSIONS=true (read SECURITY.md first).',
        ])
    else:
        # Mode 3 — docker AND bypass (the original "bypass mode"). The
        # red banner from _emit_banner has already fired before this
        # point. The summary's responsibility framing stays as today,
        # plus an explicit "containment layers remain active" line so
        # the operator knows bypass disables prompts but not the sandbox.
        mode_lines.extend([
            '  ⚠ The agent will run every tool without asking, including',
            '    Bash, Edit, Write, and any other tool. The operator who',
            '    set bypass accepts responsibility for any action the',
            '    agent takes. Containment layers (sandbox, firewall,',
            '    cap-drop) remain active and bound the blast radius of',
            '    any individual action.',
            '',
            '  See BYPASS_PROTECTIONS.md and SECURITY.md.',
        ])

    warnings: list[str] = []
    if bypass and extra_tools:
        warnings.append(
            'WARNING: bypass=true AND operator widened the allow list with: '
            + ', '.join(extra_tools)
            + '. Per-tool prompts are off; widened tools fire without operator review.'
        )
    if running_root and not bypass and not docker:
        warnings.append(
            'WARNING: running as root. The agent inherits root privileges — '
            'consider running kato as an unprivileged user.'
        )

    lines = list(header) + mode_lines
    if warnings:
        lines.append('')
        lines.extend(warnings)
    lines.append(bar)
    lines.append('')
    try:
        target.write('\n'.join(lines))
        target.flush()
    except (ValueError, OSError):
        pass
