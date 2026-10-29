"""Minimal one-shot Claude CLI invocation.

The full ``ClaudeCliClient`` builds a complex command (allowed tools,
docker wrap, session resume, MCP wiring, ...) for the implementation /
testing / review flows. The lessons subsystem just needs "send text,
get text back" — no tools, no streaming, no session state.

This helper is that minimal client. It calls ``claude -p`` as a
subprocess with the prompt on stdin, captures stdout, and returns the
text. Failures raise; the caller decides whether to log-and-continue
or surface.

"No tools" is enforced by the CLI, not assumed. A bare ``claude -p`` has the
DEFAULT tool set, and the operator's own allow rules in their user settings
approve its shell commands — so a lesson extraction fed a ticket description
went exploring the operator's home (``ls``, ``find /``, other tasks'
transcripts, the host app's logs and settings), up to 25 tool calls per run, outside
any sandbox, until it outran the timeout. ``_NO_TOOLS_FLAGS`` leaves it nothing
but its own reply.
"""

from __future__ import annotations

from typing import Callable

from agent_core_lib.agent_core_lib.helpers.one_shot import (
    DEFAULT_TIMEOUT_SECONDS,
    AgentOneShotError,
    run_one_shot,
)



#: ``--tools ""`` removes every built-in tool; MCP tools are not built-ins, so
#: ``--strict-mcp-config`` with no config removes those too. No saved session:
#: a one-shot is never resumed, and its transcript is only a copy of the prompt.
_NO_TOOLS_FLAGS = ('--tools', '', '--strict-mcp-config', '--no-session-persistence')


class OneShotError(AgentOneShotError):
    """Raised when the one-shot Claude invocation fails or times out."""


def one_shot(
    prompt: str,
    *,
    binary: str = 'claude',
    model: str = '',
    cwd: str = '',
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Send ``prompt`` to ``claude -p`` and return stdout.

    No tools, no MCP servers, no system prompt, no saved session — pure text
    completion. ``model`` is optional; empty leaves Claude on its configured
    default. ``cwd`` still points the run at a scratch dir (see
    :func:`run_one_shot`), so nothing the CLI keeps per directory lands in
    the caller's.
    """
    command: list[str] = [binary, '-p', *_NO_TOOLS_FLAGS]
    if model:
        command.extend(['--model', model])
    return run_one_shot(
        prompt,
        command=command,
        cli_name='claude',
        error_type=OneShotError,
        timeout_seconds=timeout_seconds,
        cwd=cwd,
    )


def make_one_shot(
    *,
    binary: str = 'claude',
    model: str = '',
    cwd: str = '',
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> Callable[[str], str]:
    """Return a closure that calls :func:`one_shot` with fixed config.

    ``cwd`` is bound here (the closure signature is ``(prompt) -> str``) so
    every call runs in the same isolated scratch dir.
    """
    def _call(prompt: str) -> str:
        return one_shot(
            prompt,
            binary=binary,
            model=model,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
        )
    return _call
