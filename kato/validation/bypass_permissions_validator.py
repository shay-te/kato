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
3. **Bypass on, non-interactive (CI/Docker/cron, no TTY)** -> refuse.
   With a single flag and no second-factor env var, there is no way
   to acknowledge from a non-interactive runner — kato refuses to
   start. Run kato interactively to confirm, or unset the flag.
4. **Bypass on, interactive TTY** -> prompt the operator twice with
   ``input_yes_no``. The first question is "are you sure"; the second
   is "final confirmation, this disables every per-tool prompt for
   the entire session". Both must answer yes; either no -> refuse.

The stderr banner is written *before* logger configuration runs so log
level cannot suppress it. SECURITY.md is the canonical reference.
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
    """
    if not is_bypass_enabled(env):
        return

    _emit_banner(stderr=stderr)

    if is_running_as_root():
        raise BypassPermissionsRefused(
            f'{BYPASS_ENV_KEY}=true is not allowed when running as root. '
            'Run kato as an unprivileged user. See SECURITY.md.'
        )

    if not _is_interactive_stdin(stream=stdin):
        raise BypassPermissionsRefused(
            f'{BYPASS_ENV_KEY}=true requires interactive confirmation at '
            'startup, but stdin is not a TTY (CI / Docker / cron / '
            'systemd). Either run kato interactively so the operator '
            f'can confirm at the terminal, or unset {BYPASS_ENV_KEY}.'
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
        f'  bypass permissions    : {"true" if bypass else "false"}',
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
