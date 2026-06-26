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

import subprocess
from typing import Callable

from agent_core_lib.agent_core_lib.helpers.text_utils import condensed_text


_DEFAULT_TIMEOUT_SECONDS = 120


class ClaudeOneShotError(RuntimeError):
    """Raised when the one-shot Claude invocation fails or times out."""


def claude_one_shot(
    prompt: str,
    *,
    binary: str = 'claude',
    model: str = '',
    cwd: str = '',
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Send ``prompt`` to ``claude -p`` and return stdout.

    No allowed-tools list, no system prompt, no session id — pure
    text completion. ``model`` is optional; empty leaves Claude on
    its configured default.

    ``cwd`` runs the subprocess in that directory (empty = inherit the
    caller's process cwd). Even a tool-less ``claude -p`` still writes a
    throwaway transcript under ``~/.claude/projects/<encoded-cwd>``, so
    callers that fire many one-shots should point this at an isolated
    scratch dir — otherwise the transcripts pile up in whatever repo the
    host process happens to run in and clutter the operator's own Claude
    session history.
    """
    command: list[str] = [binary, '-p']
    if model:
        command.extend(['--model', model])
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
            timeout=timeout_seconds,
            cwd=cwd or None,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeOneShotError(
            f'claude one-shot did not finish within {timeout_seconds}s'
        ) from exc
    except OSError as exc:
        raise ClaudeOneShotError(
            f'failed to invoke claude binary "{binary}": {exc}'
        ) from exc
    if completed.returncode != 0:
        stderr = (completed.stderr or '').strip()
        stdout = condensed_text(completed.stdout)
        detail = stderr or stdout or '<no output>'
        raise ClaudeOneShotError(
            f'claude one-shot exited {completed.returncode}: {detail}'
        )
    return completed.stdout or ''


def make_claude_one_shot(
    *,
    binary: str = 'claude',
    model: str = '',
    cwd: str = '',
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> Callable[[str], str]:
    """Return a closure that calls :func:`claude_one_shot` with fixed config.

    ``cwd`` is bound here (the closure signature is ``(prompt) -> str``) so
    every call runs in the same isolated scratch dir — see
    :func:`claude_one_shot`.
    """
    def _call(prompt: str) -> str:
        return claude_one_shot(
            prompt,
            binary=binary,
            model=model,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
        )
    return _call
