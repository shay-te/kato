"""Minimal one-shot Claude CLI invocation.

The full ``ClaudeCliClient`` builds a complex command (allowed tools,
docker wrap, session resume, MCP wiring, ...) for the implementation /
testing / review flows. The lessons subsystem just needs "send text,
get text back" — no tools, no streaming, no session state.

This helper is that minimal client. It calls ``claude -p`` as a
subprocess with the prompt on stdin, captures stdout, and returns the
text. Failures raise; the caller decides whether to log-and-continue
or surface.
"""

from __future__ import annotations

from typing import Callable

from agent_core_lib.agent_core_lib.helpers.one_shot import (
    DEFAULT_TIMEOUT_SECONDS,
    AgentOneShotError,
    run_one_shot,
)



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

    No allowed-tools list, no system prompt, no session id — pure text
    completion. ``model`` is optional; empty leaves Claude on its configured
    default. See :func:`run_one_shot` for ``cwd`` and why it matters here:
    even a tool-less ``claude -p`` writes a throwaway transcript under
    ``~/.claude/projects/<encoded-cwd>``.
    """
    command: list[str] = [binary, '-p']
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
