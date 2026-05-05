"""Minimal one-shot Claude CLI invocation.

The full ``ClaudeCliClient`` builds a complex command (allowed tools,
docker wrap, session resume, MCP wiring, ...) for the implementation /
testing / review flows. The lessons subsystem just needs "send text,
get text back" — no tools, no streaming, no session state.

This helper is that minimal client. It calls ``claude -p`` as a
subprocess with the prompt on stdin, captures stdout, and returns the
text. Failures raise; the caller decides whether to log-and-continue
or surface.

Used by :class:`LessonsService` for both extraction and compaction.
"""

from __future__ import annotations

import subprocess
from typing import Callable


_DEFAULT_TIMEOUT_SECONDS = 120


class ClaudeOneShotError(RuntimeError):
    """Raised when the one-shot Claude invocation fails or times out."""


def claude_one_shot(
    prompt: str,
    *,
    binary: str = 'claude',
    model: str = '',
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Send ``prompt`` to ``claude -p`` and return stdout.

    No allowed-tools list, no system prompt, no session id — pure
    text completion. ``model`` is optional; empty leaves Claude on
    its configured default.
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
        raise ClaudeOneShotError(
            f'claude one-shot exited {completed.returncode}: {stderr or "<no stderr>"}'
        )
    return completed.stdout or ''


def make_claude_one_shot(
    *,
    binary: str = 'claude',
    model: str = '',
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> Callable[[str], str]:
    """Return a closure that calls :func:`claude_one_shot` with fixed config.

    Convenience for wiring into services that take an
    ``llm_one_shot: Callable[[str], str]`` parameter (notably
    :class:`LessonsService`) so they don't need to know about the
    binary path or model selection.
    """
    def _call(prompt: str) -> str:
        return claude_one_shot(
            prompt,
            binary=binary,
            model=model,
            timeout_seconds=timeout_seconds,
        )
    return _call
