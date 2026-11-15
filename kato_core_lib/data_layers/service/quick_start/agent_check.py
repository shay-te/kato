"""Is Claude Code ready to work — installed, and signed in?

Read fresh on every ask (no cache): the quick start screen asks while the
operator installs it or signs in, and must see the change at once. ``logged_in``
is None when the CLI cannot say — never read as signed out.

When it is not installed, ``install`` says whether kato can install it here
(the same plan the in-app upgrade runs: npm when the host has it, otherwise
Anthropic's native installer) and the exact command, shown before it runs.
"""

from __future__ import annotations

from collections.abc import Callable

from kato_core_lib.helpers.agent_version_utils import (
    add_native_install_dir_to_path,
    binary_for_backend,
    installed_version,
    upgrade_plan,
)

CLAUDE = 'claude'


def claude_status(
    env: dict,
    *,
    version_of: Callable[..., str | None] = installed_version,
    login_status: Callable[[str], dict] | None = None,
    plan_for: Callable[..., dict] = upgrade_plan,
) -> dict:
    """``{installed, version, logged_in, binary, install}``."""
    # A CLI installed a moment ago (here, or in a terminal) lands in
    # ~/.local/bin, which this process's PATH may not have yet.
    add_native_install_dir_to_path()
    binary = binary_for_backend(CLAUDE, env)
    version = version_of(env, backend=CLAUDE)
    status = {
        'installed': version is not None, 'version': version or '',
        'logged_in': None, 'binary': binary, 'install': None,
    }
    if version is None:
        plan = plan_for(env, CLAUDE)
        status['install'] = {
            'available': bool(plan['allowed']), 'command': plan['command'], 'reason': plan['reason'],
        }
        return status
    status['logged_in'] = (login_status or _login_status)(binary)['logged_in']
    return status


def _login_status(binary: str) -> dict:
    from claude_core_lib.claude_core_lib.helpers.auth_status import claude_login_status
    return claude_login_status(binary)
