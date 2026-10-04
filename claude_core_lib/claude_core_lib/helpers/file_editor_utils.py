"""A one-shot Claude run that may read and edit exactly ONE file.

``one_shot`` is text in, text out: no tools at all. Some jobs need more than
that and far less than a full agent session — keeping a single document in
order is one. Regenerating the document as text does not scale (the answer
grows with the file, and one bad completion replaces all of it); a run that
can search the file and make targeted edits does, and it is the only way the
document gets better as it is written to instead of merely longer.

The run is boxed in by the CLI itself, not by the prompt:

* ``--tools`` names the ONLY tools that exist for it — read, search, edit. No
  shell, no network, no sub-agents, no file creation.
* the allow rules grant those tools on the one file and nothing else, in the
  absolute ``//`` form (see ``absolute_rule_path``).
* ``--strict-mcp-config`` with no config means no MCP servers.

So whatever text ends up in the prompt, the worst a run can do is edit the
file it was asked to edit.
"""

from __future__ import annotations

from typing import Callable

from agent_core_lib.agent_core_lib.helpers.one_shot import (
    DEFAULT_TIMEOUT_SECONDS,
    run_one_shot,
)
from claude_core_lib.claude_core_lib.helpers.one_shot_utils import OneShotError
from claude_core_lib.claude_core_lib.helpers.write_scope_settings import (
    absolute_rule_path,
)

#: Everything the editor can do. ``Edit`` covers every way of changing the
#: file; ``Write`` is deliberately absent, so a run cannot replace the whole
#: document in one call or create a file beside it.
EDITOR_TOOLS = ('Read', 'Grep', 'Edit')


def file_editor_command(file_path: str, *, binary: str = 'claude', model: str = '') -> list[str]:
    """The ``claude -p`` command for a run confined to ``file_path``."""
    rule_path = absolute_rule_path(file_path)
    command: list[str] = [
        binary, '-p',
        '--tools', ','.join(EDITOR_TOOLS),
        '--allowedTools', f'Read({rule_path})', f'Edit({rule_path})', 'Grep',
        '--strict-mcp-config',
        '--no-session-persistence',
    ]
    if model:
        command.extend(['--model', model])
    return command


def make_file_editor(
    file_path: str,
    *,
    binary: str = 'claude',
    model: str = '',
    cwd: str = '',
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> Callable[[str], str]:
    """Return ``(prompt) -> reply`` for a run that can edit only ``file_path``.

    ``cwd`` should be a scratch directory that does NOT contain the file: the
    allow rules are what reach it, so nothing else near it comes along.
    """
    command = file_editor_command(file_path, binary=binary, model=model)

    def _call(prompt: str) -> str:
        return run_one_shot(
            prompt,
            command=command,
            cli_name='claude',
            error_type=OneShotError,
            timeout_seconds=timeout_seconds,
            cwd=cwd,
        )
    return _call
