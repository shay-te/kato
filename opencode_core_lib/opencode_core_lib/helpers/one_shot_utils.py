"""Minimal one-shot OpenCode CLI invocation.

OpenCode's half of the one-shot path: the command and where the answer lands.
The subprocess plumbing — stdin, timeout, exit-code handling — is shared with
every other CLI agent in ``agent_core_lib.helpers.one_shot``.

Callers that need "send text, get text back" — no tools, no session state —
get ``opencode run`` with the prompt on stdin and read-only permission rules
(edits, shell and fetches denied). The reply is read off the JSON event stream.
"""

from __future__ import annotations

import os
import subprocess
from typing import Callable

from agent_core_lib.agent_core_lib.helpers.one_shot import (
    DEFAULT_TIMEOUT_SECONDS,
    AgentOneShotError,
    run_one_shot,
)
from agent_core_lib.agent_core_lib.session.turn_result import parse_json_event, turn_result
from opencode_core_lib.opencode_core_lib.helpers.cli_generation import cli_generation
from opencode_core_lib.opencode_core_lib.session.wire_protocol import (
    MODE_READ_ONLY,
    model_arguments,
    run_arguments,
    run_environment,
    translate_event,
)


class OneShotError(AgentOneShotError):
    """Raised when the one-shot OpenCode invocation fails or times out."""


def one_shot(
    prompt: str,
    *,
    binary: str = 'opencode',
    model: str = '',
    cwd: str = '',
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Send ``prompt`` to ``opencode run`` and return the agent's final text.

    ``model`` (``provider/model``) is optional; empty leaves OpenCode on the
    operator's configured default.
    """
    generation = cli_generation(binary)
    command = [*run_arguments([binary], generation), *model_arguments(model, '', generation)]
    return run_one_shot(
        prompt,
        command=command,
        cli_name='opencode',
        error_type=OneShotError,
        timeout_seconds=timeout_seconds,
        cwd=cwd,
        read_output=_final_text,
        env={**os.environ, **run_environment(cwd, mode=MODE_READ_ONLY)},
    )


def _final_text(completed: subprocess.CompletedProcess) -> str:
    """The reply off the JSON event stream; an ``error`` line is an error."""
    events = []
    for line in (completed.stdout or '').splitlines():
        raw = parse_json_event(line)
        if raw is not None:
            events.extend(translate_event(raw))
    result = turn_result(events)
    if result['is_error']:
        raise OneShotError(f'opencode one-shot failed: {result["result"] or "unknown error"}')
    return str(result['result'] or '')


def make_one_shot(
    *,
    binary: str = 'opencode',
    model: str = '',
    cwd: str = '',
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> Callable[[str], str]:
    """Return a closure that calls :func:`one_shot` with fixed config."""
    def _call(prompt: str) -> str:
        return one_shot(
            prompt,
            binary=binary,
            model=model,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
        )
    return _call
