"""Minimal one-shot Grok CLI invocation.

Grok's half of the one-shot path: the command and where the answer lands. The
subprocess plumbing — timeout, exit-code handling — is shared with every other
CLI agent in ``agent_core_lib.helpers.one_shot``.

Callers that need "send text, get text back" — no tools, no session state —
get ``grok`` in its read-only sandbox with edits denied. Headless Grok never
reads stdin, so the prompt goes in a temporary file (``--prompt-file``); the
reply is read off the JSONL stream.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Callable

from agent_core_lib.agent_core_lib.helpers.one_shot import (
    DEFAULT_TIMEOUT_SECONDS,
    AgentOneShotError,
    run_one_shot,
)
from agent_core_lib.agent_core_lib.session.turn_result import parse_json_event, turn_result
from grok_core_lib.grok_core_lib.session.wire_protocol import (
    SANDBOX_READ_ONLY,
    base_arguments,
    permission_arguments,
    translate_event,
)


class OneShotError(AgentOneShotError):
    """Raised when the one-shot Grok invocation fails or times out."""


def one_shot(
    prompt: str,
    *,
    binary: str = 'grok',
    model: str = '',
    cwd: str = '',
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Send ``prompt`` to ``grok`` and return the agent's final text.

    ``model`` is optional; empty leaves Grok on the operator's default.
    """
    folder = tempfile.mkdtemp(prefix='grok-oneshot-')
    try:
        prompt_file = os.path.join(folder, 'prompt.md')
        with open(prompt_file, 'w', encoding='utf-8') as handle:
            handle.write(prompt)
        command = [
            *base_arguments(binary), '--prompt-file', prompt_file,
            '--no-subagents', *permission_arguments(sandbox=SANDBOX_READ_ONLY),
        ]
        if model:
            command.extend(['-m', model])
        return run_one_shot(
            '',
            command=command,
            cli_name='grok',
            error_type=OneShotError,
            timeout_seconds=timeout_seconds,
            cwd=cwd,
            read_output=_final_text,
        )
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _final_text(completed: subprocess.CompletedProcess) -> str:
    """The reply off the JSONL stream; a refused turn is an error, not a reply."""
    events = []
    for line in (completed.stdout or '').splitlines():
        raw = parse_json_event(line)
        if raw is not None:
            events.extend(translate_event(raw))
    result = turn_result(events)
    if result['is_error']:
        raise OneShotError(f'grok one-shot failed: {result["result"] or "unknown error"}')
    return str(result['result'] or '')


def make_one_shot(
    *,
    binary: str = 'grok',
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
