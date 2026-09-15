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

import hashlib
import json
import tempfile
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json
import os
import sys

from agent_core_lib.agent_core_lib.helpers.sandbox_scope import (
    effective_sandbox_roots,
)
# The task folder and its memory directory come from the SAME helpers the
# workspace-scope prompt uses, so the path the agent is told and the path its
# CLI actually loads memory from cannot drift apart again.
from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    task_folder_for,
    task_memory_directory,
)

_READ_DEDUPE_MODULE = 'agent_core_lib.agent_core_lib.helpers.read_dedupe'
_LESSONS_GATE_MODULE = 'agent_core_lib.agent_core_lib.helpers.lessons_gate'

# Read from the environment rather than passed in: the settings builder is
# called from several places and every one of them would have to thread the
# path through. The hook itself reads the same variable, so there is one
# source for 'is there a lessons file'.
from agent_core_lib.agent_core_lib.helpers.lessons_gate import (
    LESSONS_PATH_ENV,
)
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


def lessons_gate_hook_settings() -> dict:
    """``PreToolUse`` hook that blocks every tool until the lessons file is read.

    The lessons text is no longer pasted into the system prompt — it pushed
    the Windows spawn command line past its 32,767-character limit — so the
    prompt names the path instead. Prompt wording is a request; this is the
    enforcement, and the two ship together: without it "read this file" would
    be advice the agent is free to skip.

    ``Read`` is never gated, so there is always a way to satisfy it. See
    ``agent_core_lib.helpers.lessons_gate`` for the fail-open rules.

    Matcher ``*`` rather than a tool list: a new CLI capability must be gated
    the moment it exists, not whenever someone remembers to add it here.
    """
    return {'hooks': {'PreToolUse': [{
        'matcher': '*',
        'hooks': [{
            'type': 'command',
            'command': f'{sys.executable} -m {_LESSONS_GATE_MODULE}',
        }],
    }]}}


def _absolute_rule_path(path: str) -> str:
    """``path`` in the form a permission rule reads as ABSOLUTE.

    A rule path with ONE leading slash is resolved relative to the settings
    file, not the filesystem root; ``//`` is what means absolute. The deny
    rules below used the single-slash form and matched nothing at all.

    Verified against the real CLI (2.1.270), each with a control in the same
    run proving the operation otherwise succeeds:

    * ``Edit(/Users/me/.claude/**)`` let a write through; ``Edit(//Users/me/
      .claude/**)`` refused it — even under ``bypassPermissions``;
    * ``Write(//Users/me/.claude/**)`` ALSO let the write through, so file
      rules are written against ``Edit``, which covers every writing tool;
    * ``Read(/Users/me/...)`` let a read through; ``Read(//...)`` refused it.

    POSIX-shaped on every platform (``//C:/Users/me``); the Windows form is
    not verified.
    """
    return '//' + Path(path).as_posix().lstrip('/')


def agent_state_dir_write_deny_rules() -> list[str]:
    """Deny-rules for the CLI's OWN state directory (``~/.claude``).

    The ask-rules below cannot cover this. The CLI treats its own state
    directory as a scratch path and auto-accepts writes there, so the ask
    never fires — the operator sees "wrote OUTSIDE the task folder ... no
    approval was requested" AFTER the fact, which is a report, not a
    control.

    That is where the CLI's built-in memory feature puts its notes, so an
    agent told "your memory directory is <task>/memory/" still wrote to
    ``~/.claude/projects/<encoded-cwd>/memory/``: the CLI's own instruction
    names a fixed per-user path and beats prompt guidance. Guidance has now
    failed twice; a deny rule is the only thing that actually holds.

    Denies WRITES. Reading memory there is refused separately and narrowly
    (``agent_memory_read_deny_rules``); the rest of the directory stays
    readable, and the CLI itself keeps writing its transcripts, because that
    is not a tool call.
    """
    # ONE ``Edit`` rule in the absolute ``//`` form — see
    # ``_absolute_rule_path`` for the live verification of both choices. The
    # per-tool single-slash rules this replaced never matched a single write.
    home = _absolute_rule_path(os.path.expanduser('~'))
    return [f'Edit({home}/.claude/**)']


def agent_memory_read_deny_rules() -> list[str]:
    """Deny-rules for READING memory under the CLI's per-user directory.

    Memory belongs to the task folder. Reading
    ``~/.claude/projects/<any encoded path>/memory/`` would pull notes from a
    different task — or from before memory moved into the task — straight into
    this one. Scoped to the memory folders only; everything else the CLI keeps
    in that directory stays readable.
    """
    home = _absolute_rule_path(os.path.expanduser('~'))
    return [f'Read({home}/.claude/projects/**/memory/**)']


def auto_memory_directory_setting(cwd: str = '') -> dict:
    """Pin the CLI's OWN memory location inside the task folder.

    Prompt guidance lost to the CLI's built-in memory feature twice: the CLI
    tells the agent its memory lives at ``~/.claude/projects/<cwd>/memory/``,
    and that specific built-in instruction beats a general one. The write
    denial then blocked those writes, which left the agent stuck rather than
    pointed anywhere — so the operator had to step in and redirect it by hand,
    every task.

    ``autoMemoryDirectory`` changes the answer at the source: the CLI loads and
    saves memory in the task folder, and its own system prompt names that
    folder, so nothing is left competing with the workspace-scope prompt.
    Verified against the real CLI: a ``MEMORY.md`` planted in the configured
    directory was known to the agent, and unknown without the setting.

    Empty when no task folder can be identified. Never a guess, and never a
    repository clone — that would put memory one ``git add`` from a commit.
    """
    memory = task_memory_directory(task_folder_for(cwd))
    return {'autoMemoryDirectory': memory} if memory else {}


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
        # Deny beats allow and ask: the CLI auto-accepts writes to its own
        # state directory, so nothing softer than a denial keeps the agent's
        # memory out of the global agent folder; and memory there is not read
        # back into this task either.
        'deny': agent_state_dir_write_deny_rules() + agent_memory_read_deny_rules(),
    }}
    # The positive half: tell the CLI where memory DOES go. The denials above
    # only stop the wrong place; this is what stops the agent needing to be
    # told, by hand, on every new task.
    settings.update(auto_memory_directory_setting(cwd))
    hooks: list = []
    if dedupe_reads:
        hooks.extend(read_dedupe_hook_settings()['hooks']['PreToolUse'])
    # Gated on the lessons path being CONFIGURED. ``read_lessons_file``
    # injects its directive only for a file that exists and is non-empty, and
    # the caller sets this env var on the same condition — so the gate and the
    # instruction that tells the agent how to satisfy it are never out of step.
    # A gate with no matching directive would deny every tool while nothing on
    # screen said why.
    if str(os.environ.get(LESSONS_PATH_ENV, '') or '').strip():
        hooks.extend(lessons_gate_hook_settings()['hooks']['PreToolUse'])
    # MERGED, not ``update``d. Two ``settings.update({'hooks': ...})`` calls
    # would leave only the last one's PreToolUse list — the read-dedupe hook
    # would vanish the moment a lessons file existed.
    if hooks:
        settings['hooks'] = {'PreToolUse': hooks}
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


def out_of_workspace_write_settings_path(
    cwd: str = '',
    additional_dirs: tuple[str, ...] | list[str] = (),
    dedupe_reads: bool | None = None,
) -> str:
    """The same settings, written to a file — pass the PATH to ``--settings``.

    The JSON carries one rule per directory, so a 25-repo workspace produces
    roughly 8KB. Inline on the command line that is enough to blow Windows'
    limit on its own, and the spawn dies before the agent ever starts:

        send failed: failed to launch claude CLI binary "claude":
        [WinError 206] The filename or extension is too long

    ``_build_command`` already notes that cmd.exe caps a command line at
    ~8K — the settings value alone reaches that, and the multiline
    ``--append-system-prompt`` is then pure overflow. A path is ~60
    characters whatever the workspace holds.

    Written OUTSIDE the workspace on purpose: this file is what forces
    out-of-workspace writes back through the approval path, so putting it
    somewhere the agent can edit would let it widen its own permissions.
    Returns '' if the file cannot be written, so the caller can fall back to
    the inline string rather than failing the spawn.
    """
    payload = out_of_workspace_write_settings(cwd, additional_dirs, dedupe_reads)
    try:
        directory = Path(tempfile.gettempdir()) / 'agent-write-scope-settings'
        directory.mkdir(parents=True, exist_ok=True)
        # Named for the workspace it describes, so two concurrent tasks never
        # share one file and a re-spawn reuses its own.
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode('utf-8'),
        ).hexdigest()[:16]
        target = directory / f'settings-{digest}.json'
        atomic_write_json(target, payload)
        return str(target)
    except Exception:
        return ''
