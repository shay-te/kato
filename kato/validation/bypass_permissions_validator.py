"""Hard-stop safety gate for ``KATO_CLAUDE_BYPASS_PERMISSIONS=true``.

When the bypass flag is on, the agent runs every tool (Bash, Edit, Write,
Read, anything Claude exposes) without asking. That removes the planning UI
permission layer entirely. This validator runs before kato wires its
services and decides whether to allow the process to continue.

Decisions (in order):

1. **Bypass off** -> nothing to do.
2. **Bypass on, running as root** -> refuse. Root + bypass + agent =
   the worst possible blast radius. There is no legitimate reason to
   combine the three.
3. **Bypass on, ``KATO_CLAUDE_BYPASS_PERMISSIONS_ACCEPT=true`` set** ->
   allow. The operator has acknowledged the risk in writing in their
   ``.env``. We still print a stderr banner.
4. **Bypass on, interactive TTY, no acknowledgement** -> prompt the
   operator with ``input_yes_no`` (from ``core_lib``). Refuse on no.
   On yes, kato still runs only this once; the operator must set
   ``KATO_CLAUDE_BYPASS_PERMISSIONS_ACCEPT=true`` to skip the prompt.
5. **Bypass on, non-interactive (CI/Docker/cron), no acknowledgement**
   -> refuse with a clear message naming the env var to set.

The stderr banner is written *before* logger configuration runs so log
level cannot suppress it. SECURITY.md is the canonical reference.
"""

from __future__ import annotations

import os
import sys

try:
    from core_lib.helpers.command_line import input_yes_no as _core_input_yes_no
except (ImportError, ModuleNotFoundError):  # pragma: no cover - core-lib is required
    _core_input_yes_no = None


BYPASS_ENV_KEY = 'KATO_CLAUDE_BYPASS_PERMISSIONS'
ACCEPT_ENV_KEY = 'KATO_CLAUDE_BYPASS_PERMISSIONS_ACCEPT'
TRUE_VALUES = frozenset({'1', 'true', 'yes', 'on'})

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


def is_accept_acknowledged(env: dict | None = None) -> bool:
    source = env if env is not None else os.environ
    return str(source.get(ACCEPT_ENV_KEY, '')).strip().lower() in TRUE_VALUES


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
    """
    if not is_bypass_enabled(env):
        return

    _emit_banner(stderr=stderr)

    if is_running_as_root():
        raise BypassPermissionsRefused(
            f'{BYPASS_ENV_KEY}=true is not allowed when running as root. '
            'Run kato as an unprivileged user. See SECURITY.md.'
        )

    if is_accept_acknowledged(env):
        return

    if not _is_interactive_stdin(stream=stdin):
        raise BypassPermissionsRefused(
            f'{BYPASS_ENV_KEY}=true requires explicit acknowledgement when '
            'running non-interactively (CI, Docker, cron, systemd, etc.). '
            f'Set {ACCEPT_ENV_KEY}=true in your .env to acknowledge that '
            'the operator accepts responsibility for the agent running '
            'every tool without asking. See SECURITY.md.'
        )

    prompter = yes_no_prompter if yes_no_prompter is not None else _core_input_yes_no
    if prompter is None:  # pragma: no cover - core-lib is required
        raise BypassPermissionsRefused(
            'core-lib input_yes_no helper is unavailable; cannot prompt. '
            f'Set {ACCEPT_ENV_KEY}=true in your .env to bypass the prompt.'
        )

    answered_yes = bool(
        prompter(
            (
                f'{BYPASS_ENV_KEY}=true. The agent will run every tool '
                'without asking, including Bash, Edit, and Write. Are you '
                'sure you want to continue?'
            ),
            False,
        )
    )
    if not answered_yes:
        raise BypassPermissionsRefused(
            'Operator declined the bypass-permissions prompt. Aborting.'
        )


_SAFE_DEFAULT_ALLOWED_TOOLS = frozenset({'Edit', 'Write', 'Read', 'Bash', 'Glob', 'Grep'})


def print_security_posture(*, env: dict | None = None, stderr=None) -> None:
    """Always-visible boot banner summarizing the security posture.

    Bypassing log level: writes to stderr directly. The intent is that an
    operator scanning the kato boot output sees, at one glance, every
    flag that affects what the agent can do.
    """
    source = env if env is not None else os.environ
    target = stderr if stderr is not None else sys.stderr

    bypass = is_bypass_enabled(source)
    accept = is_accept_acknowledged(source)
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

    lines = [
        '',
        '=' * 78,
        ' kato — security posture at boot',
        '=' * 78,
        f'  agent backend         : {backend}',
        f'  bypass permissions    : {"true" if bypass else "false"}'
        + ('  (operator acknowledged via .env)' if bypass and accept else ''),
        f'  running as root       : {"yes" if running_root else "no"}',
        f'  architecture doc      : {arch_doc or "(not set)"}',
        f'  allowed-tools (extra) : {", ".join(extra_tools) if extra_tools else "(safe default only)"}',
        f'  git operations by Claude: BLOCKED (Bash(git:*) on every spawn)',
    ]
    warnings: list[str] = []
    if bypass and extra_tools:
        warnings.append(
            'WARNING: bypass=true AND operator widened the allow list with: '
            + ', '.join(extra_tools)
            + '. Per-tool prompts are off; widened tools fire without operator review.'
        )
    if running_root and not bypass:
        warnings.append(
            'WARNING: running as root. The agent inherits root privileges — '
            'consider running kato as an unprivileged user.'
        )
    if warnings:
        lines.append('')
        lines.extend(warnings)
    lines.append('=' * 78)
    lines.append('')
    try:
        target.write('\n'.join(lines))
        target.flush()
    except (ValueError, OSError):
        pass
