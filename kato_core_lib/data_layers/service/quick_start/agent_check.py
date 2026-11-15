"""Is the operator's agent ready to work — installed, and signed in?

Any of the four chat agents (Claude Code, Codex, Grok, OpenCode): quick start
lets the operator pick the one they use and checks THAT one. Read fresh on every
ask (no cache): the screen asks while the operator installs it or signs in, and
must see the change at once.

* Installed: the CLI answers ``--version``. When it does not, ``install`` says
  whether kato can install it here (the in-app upgrade's own plan: npm when the
  host has it, Anthropic's native installer for Claude Code) and ``help`` is the
  CLI's own words for doing it by hand (its transport's install text).
* Signed in: only Claude Code can say (``claude auth status``), and kato runs
  its sign-in from the screen. For the others ``login_command`` names their own
  sign-in (``codex login`` …); kato cannot tell whether it was done, so an
  installed one counts as ready and the CLI itself asks at the first message.
  ``logged_in`` None is "cannot tell" — never read as signed out.
"""

from __future__ import annotations

from collections.abc import Callable

from kato_core_lib.helpers.agent_backend_readiness import readiness_error
from kato_core_lib.helpers.agent_version_utils import (
    add_agent_install_dirs_to_path,
    binary_for_backend,
    download_page,
    installed_version,
    upgrade_plan,
)
from kato_core_lib.helpers.chat_agent_utils import CHAT_AGENTS

CLAUDE = 'claude'
LOGIN_COMMANDS = {
    'codex': 'codex login',
    'grok': 'grok login',
    'opencode': 'opencode auth login',
}


def agent_status(
    agent: str,
    env: dict,
    *,
    version_of: Callable[..., str | None] = installed_version,
    login_status: Callable[[str], dict] | None = None,
    plan_for: Callable[..., dict] = upgrade_plan,
    readiness: Callable[[str, str], str] = readiness_error,
) -> dict:
    """``{agent, installed, version, logged_in, binary, install, help,
    login_command, download}``."""
    # A Claude Code installed a moment ago (here, or in a terminal) lands in a
    # folder this process's PATH may not have yet.
    add_agent_install_dirs_to_path()
    binary = binary_for_backend(agent, env)
    version = version_of(env, backend=agent)
    status = {
        'agent': agent, 'installed': version is not None, 'version': version or '',
        'logged_in': None, 'binary': binary, 'install': None, 'help': '',
        'login_command': '', 'download': download_page(agent),
    }
    if version is None:
        plan = plan_for(env, agent)
        status['install'] = {
            'available': bool(plan['allowed']), 'command': plan['command'], 'reason': plan['reason'],
        }
        status['help'] = readiness(agent, binary)
        return status
    if agent == CLAUDE:
        status['logged_in'] = (login_status or _login_status)(binary)['logged_in']
    else:
        status['login_command'] = LOGIN_COMMANDS.get(agent, '')
    return status


def all_agents_status(env: dict, **options) -> dict:
    """Every chat agent's status, in tab order."""
    return {agent: agent_status(agent, env, **options) for agent in CHAT_AGENTS}


def _login_status(binary: str) -> dict:
    from claude_core_lib.claude_core_lib.helpers.auth_status import claude_login_status
    return claude_login_status(binary)
