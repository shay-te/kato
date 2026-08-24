"""Force-approval settings for out-of-workspace file writes.

``acceptEdits`` auto-accepts every Write/Edit/MultiEdit/NotebookEdit whose
path Claude Code considers in-scope. That scope did NOT reliably cover the
case that bit us: a SIBLING repo under the operator's home dir (e.g.
``~/Desktop/dev/other-repo``) that is NOT a task clone — Claude wrote there
with no prompt at all. The old ask-rules only enumerated system roots
(``/tmp``, ``/etc``, …) and DELIBERATELY skipped ``/Users``/``/home`` (a
blanket home rule would prompt on every in-workspace edit), so home-tree
siblings matched no rule and sailed through.

This module now builds task-AWARE settings — allow the sandbox, ask for
everything else:

  * ``permissions.allow`` — the write tools scoped to the task's sandbox
    roots (``cwd`` + ``--add-dir`` clones + the task-folder parent — the
    SAME boundary the post-hoc classifier uses). In-workspace edits
    auto-accept.
  * ``permissions.ask`` — the write tools UNSCOPED (bare tool name = every
    invocation). Claude Code precedence is ``deny > allow > ask``, so a
    write that matches an allow rule (in-workspace) is auto-accepted and
    never reaches the ask rule; a write ANYWHERE else — any sibling repo,
    any home path, ``/tmp``, everything — matches only the ask rule and is
    forced to the operator's approval. Nothing can slip through
    un-enumerated any more.

With NO workspace known (``cwd`` empty — e.g. the boot smoke test) there is
nothing to allow, so every write prompts: the fail-safe direction.

The post-hoc warning (``_maybe_warn_out_of_sandbox_write`` in
``session/streaming.py``) stays as a last-resort visibility backstop.
"""
from __future__ import annotations

import json
import os
import sys

from agent_core_lib.agent_core_lib.helpers.sandbox_scope import (
    effective_sandbox_roots,
)

_READ_DEDUPE_MODULE = 'agent_core_lib.agent_core_lib.helpers.read_dedupe'
# Generic (product-agnostic) switch; the orchestrator bridges its own config
# name onto it. Off unless explicitly turned on — the hook withholds content
# from the agent, which is not a default anyone should get by surprise.
READ_DEDUPE_ENABLED_ENV = 'AGENT_READ_DEDUPE_ENABLED'


def read_dedupe_enabled() -> bool:
    value = str(os.environ.get(READ_DEDUPE_ENABLED_ENV, '') or '').strip().lower()
    return value in ('1', 'true', 'yes', 'on')

# File-mutating tools that ``acceptEdits`` auto-accepts. Bash is NOT here: it
# already routes through the permission callback under ``acceptEdits``, so an
# out-of-folder ``bash`` write is already gated by the orchestrator.
_WRITE_TOOLS: tuple[str, ...] = ('Write', 'Edit', 'MultiEdit', 'NotebookEdit')


def in_workspace_write_allow_rules(
    cwd: str = '', additional_dirs: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """``Tool(root/**)`` allow-rules for each write tool × each sandbox root,
    so edits INSIDE the task folder auto-accept without a prompt. Empty when
    no workspace is known (then every write falls to the ask rule)."""
    roots = effective_sandbox_roots(cwd, additional_dirs)
    return [
        f'{tool}({root}/**)'
        for root in roots
        for tool in _WRITE_TOOLS
    ]


def out_of_workspace_write_ask_rules() -> list[str]:
    """Unscoped catch-all ask-rules — every write-tool invocation prompts
    UNLESS an allow rule (in-workspace) matches first. This is what forces
    approval for ANY out-of-workspace write, including sibling repos under
    the home tree that no enumerated root list could cover."""
    return list(_WRITE_TOOLS)


def read_dedupe_hook_settings() -> dict:
    """``PreToolUse`` hook that blocks re-reads of unchanged files.

    See ``agent_core_lib.helpers.read_dedupe`` for the measurement that motivates it and
    the escape hatches. Opt-in: the caller decides whether to include this,
    because it changes what the agent can retrieve.

    Invoked as ``<this interpreter> -m <module>`` so the hook runs in the
    same environment as the caller, with no separate script file to install
    or keep in sync.
    """
    return {'hooks': {'PreToolUse': [{
        'matcher': 'Read',
        'hooks': [{
            'type': 'command',
            'command': f'{sys.executable} -m {_READ_DEDUPE_MODULE}',
        }],
    }]}}


def out_of_workspace_write_settings(
    cwd: str = '',
    additional_dirs: tuple[str, ...] | list[str] = (),
    dedupe_reads: bool | None = None,
) -> dict:
    """Settings dict that forces approval for out-of-workspace file writes.

    ``dedupe_reads`` includes the read-dedupe ``PreToolUse`` hook; ``None``
    (the default) defers to ``AGENT_READ_DEDUPE_ENABLED``, which is off
    unless an operator turns it on — the hook withholds content from the
    agent, so nobody should get it by surprise.
    """
    if dedupe_reads is None:
        dedupe_reads = read_dedupe_enabled()
    settings = {'permissions': {
        'allow': in_workspace_write_allow_rules(cwd, additional_dirs),
        'ask': out_of_workspace_write_ask_rules(),
    }}
    if dedupe_reads:
        settings.update(read_dedupe_hook_settings())
    return settings


def out_of_workspace_write_settings_json(
    cwd: str = '',
    additional_dirs: tuple[str, ...] | list[str] = (),
    dedupe_reads: bool | None = None,
) -> str:
    """The settings as a compact JSON string for ``claude --settings``."""
    return json.dumps(
        out_of_workspace_write_settings(cwd, additional_dirs, dedupe_reads),
        separators=(',', ':'),
    )
