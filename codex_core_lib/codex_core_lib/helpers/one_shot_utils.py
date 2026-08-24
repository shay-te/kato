"""Minimal one-shot Codex CLI invocation.

Codex's half of the one-shot path: the command and where the answer lands.
The subprocess plumbing — stdin, timeout, exit-code handling — is shared with
every other CLI agent in ``agent_core_lib.helpers.one_shot``.

The lessons subsystem just needs "send text, get text back" — no
tools, no streaming, no session state. We invoke ``codex exec``
with the prompt on stdin and use ``--output-last-message`` to
capture the agent's final reply cleanly (parsing the ``--json``
JSONL event stream for "the last agent_message" would tie us to
event names that aren't part of the CLI's public contract).

Verified against ``codex-cli 0.132.0``.
"""

from __future__ import annotations

import os
import tempfile
from typing import Callable

from agent_core_lib.agent_core_lib.helpers.one_shot import (
    DEFAULT_TIMEOUT_SECONDS,
    AgentOneShotError,
    run_one_shot,
)


class OneShotError(AgentOneShotError):
    """Raised when the one-shot Codex invocation fails or times out."""


def one_shot(
    prompt: str,
    *,
    binary: str = 'codex',
    model: str = '',
    cwd: str = '',
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Send ``prompt`` to ``codex exec`` and return the agent's final text.

    No tools, no system prompt, no session id — pure text completion.
    ``model`` is optional; empty leaves Codex on whatever the operator's
    ``~/.codex/config.toml`` declares as the default.
    """
    fd, last_message_file = tempfile.mkstemp(prefix='codex-oneshot-', suffix='.txt')
    os.close(fd)
    try:
        command: list[str] = [
            binary, 'exec',
            # ``read-only`` sandbox + ``never`` approval so the one-shot path
            # can't make accidental edits and never blocks on human input.
            '--sandbox', 'read-only',
            '--ask-for-approval', 'never',
            '--skip-git-repo-check',
            '-o', last_message_file,
        ]
        if model:
            command.extend(['-m', model])
        return run_one_shot(
            prompt,
            command=command,
            cli_name='codex',
            error_type=OneShotError,
            timeout_seconds=timeout_seconds,
            read_output=lambda completed: (
                _final_message(last_message_file) or completed.stdout or ''
            ),
        )
    finally:
        try:
            os.unlink(last_message_file)
        except OSError:
            pass


def _final_message(path: str) -> str:
    """The reply Codex wrote to ``-o``, or ``''`` when it wrote nothing.

    Preferred over stdout because parsing the ``--json`` event stream for
    "the last agent_message" would tie us to event names that are not part
    of the CLI's public contract.
    """
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return handle.read()
    except OSError:
        return ''


def make_one_shot(
    *,
    binary: str = 'codex',
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
