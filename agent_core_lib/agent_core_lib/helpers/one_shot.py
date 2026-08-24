"""One-shot CLI invocation: send a prompt, get the agent's text back.

The full clients build a complex command — allowed tools, sandbox wrapping,
session resume, MCP wiring — for the implementation / testing / review flows.
Some callers (lesson extraction, for one) need none of that: no tools, no
streaming, no session state, just text in and text out.

Every CLI agent does that the same way — build a command, feed the prompt on
stdin, bound it with a timeout, turn a non-zero exit into a readable error —
and differs only in the flags it takes and where it puts the answer. That
difference is the ``command`` a caller passes and the optional
``read_output`` hook; everything else is here, once.
"""

from __future__ import annotations

import subprocess
from typing import Callable, Sequence

from utils_core_lib.utils_core_lib.text_utils import condensed_text

DEFAULT_TIMEOUT_SECONDS = 120


class AgentOneShotError(RuntimeError):
    """Raised when a one-shot CLI invocation fails or times out.

    Transports subclass this so a caller can catch either the specific
    CLI's failure or any one-shot failure at all.
    """


def run_one_shot(
    prompt: str,
    *,
    command: Sequence[str],
    cli_name: str,
    error_type: type[AgentOneShotError] = AgentOneShotError,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    cwd: str = '',
    read_output: Callable[[subprocess.CompletedProcess], str] | None = None,
) -> str:
    """Run ``command`` with ``prompt`` on stdin; return the agent's text.

    ``read_output`` extracts the answer from the finished process — default
    is stdout, which is what a CLI that prints its reply needs. A CLI that
    writes its final message to a file passes a reader that prefers the file
    and falls back to stdout, so a run that produced no reply still yields
    something rather than an exception.

    ``cwd`` runs the subprocess there (empty inherits the caller's). It
    matters more than it looks: a tool-less run can still write a throwaway
    transcript under the CLI's per-directory session store, so a caller
    firing many one-shots should point this at a scratch dir instead of
    littering the operator's own session history.
    """
    try:
        completed = subprocess.run(
            list(command),
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
        raise error_type(
            f'{cli_name} one-shot did not finish within {timeout_seconds}s'
        ) from exc
    except OSError as exc:
        raise error_type(
            f'failed to invoke {cli_name} binary "{command[0]}": {exc}'
        ) from exc
    if completed.returncode != 0:
        stderr = (completed.stderr or '').strip()
        detail = stderr or condensed_text(completed.stdout) or '<no output>'
        raise error_type(
            f'{cli_name} one-shot exited {completed.returncode}: {detail}'
        )
    if read_output is not None:
        return read_output(completed)
    return completed.stdout or ''
