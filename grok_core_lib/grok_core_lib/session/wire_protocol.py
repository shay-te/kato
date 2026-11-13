"""Grok Build CLI wire protocol: ``grok -p --output-format streaming-messages-json``.

Grok has two JSONL formats. The native ``streaming-json`` reports the session
id only on its LAST line (``end``), and on a failure prints an ``error`` and
no ``end`` at all — so a failed first turn would leave the chat with no id
and no terminal event. ``streaming-messages-json`` (a Messages-API stream)
names the session on its FIRST line and always ends with a ``result``, the
error path included. This transport uses it.

Read from Grok Build's source (``src/headless/reducer/messages``)::

    {"type":"system","subtype":"init","session_id":"<uuid>","model":"…","cwd":"…"}
    {"type":"assistant","message":{"content":[{"type":"text","text":"…"},
        {"type":"thinking","thinking":"…"},
        {"type":"tool_use","id":"…","name":"…","input":{…}}]},"session_id":"…"}
    {"type":"user","message":{"content":[{"type":"tool_result",…}]}}
    {"type":"result","subtype":"success|error_max_turns|error_during_execution|…",
        "is_error":false,"result":"…","errors":[],"session_id":"…","usage":{…}}

``translate_event`` maps each line onto the shared per-turn vocabulary
(``agent_core_lib.session.turn_events``), the only shape the host draws.

The ARGV side of the protocol lives here too (``base_arguments``,
``permission_arguments``, ``deny_rules``), so the one-shot client and the chat
build a run the same way.
"""

from __future__ import annotations

import sys
from typing import Any

from agent_core_lib.agent_core_lib.helpers.command_floor import (
    GIT_ACCESS_STRICT,
    floor_command_prefixes,
)
from agent_core_lib.agent_core_lib.session.turn_events import (
    agent_message,
    command_execution,
    file_change,
    reasoning,
    session_started,
    tool_call,
    turn_completed,
    turn_failed,
    web_search,
)

GROK_EVENT_SYSTEM = 'system'
GROK_SYSTEM_SUBTYPE_INIT = 'init'
GROK_EVENT_ASSISTANT = 'assistant'
GROK_EVENT_USER = 'user'
#: The turn's last line, on success AND on failure.
GROK_EVENT_RESULT = 'result'
GROK_RESULT_SUBTYPE_SUCCESS = 'success'

#: Input keys a tool names its file by.
_PATH_KEYS = ('path', 'file_path', 'filePath', 'target_file', 'filename')
#: Tool-name words that mean the call changes a file.
_EDIT_WORDS = ('edit', 'write', 'create', 'patch', 'replace', 'delete', 'move', 'rename')


def translate_event(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """One Grok line as events in the shared vocabulary (possibly none)."""
    kind = str(raw.get('type', '') or '')
    if kind == GROK_EVENT_SYSTEM:
        session_id = str(raw.get('session_id', '') or '').strip()
        if raw.get('subtype') == GROK_SYSTEM_SUBTYPE_INIT and session_id:
            return [session_started(session_id)]
        return []
    if kind == GROK_EVENT_ASSISTANT:
        return _assistant_events(raw)
    if kind == GROK_EVENT_RESULT:
        return [_result_event(raw)]
    # ``user`` lines carry tool results back to the model: nothing to show.
    return []


def _assistant_events(raw: dict[str, Any]) -> list[dict[str, Any]]:
    message = raw.get('message')
    content = message.get('content') if isinstance(message, dict) else None
    events: list[dict[str, Any]] = []
    for block in content if isinstance(content, list) else ():
        if not isinstance(block, dict):
            continue
        block_type = block.get('type')
        if block_type == 'text' and str(block.get('text', '')).strip():
            events.append(agent_message(block['text']))
        elif block_type == 'thinking' and str(block.get('thinking', '')).strip():
            events.append(reasoning(block['thinking']))
        elif block_type == 'tool_use':
            events.append(tool_event(str(block.get('name', '') or ''), block.get('input')))
    return events


def tool_event(name: str, tool_input: Any) -> dict[str, Any]:
    """A tool call as the item the chat already knows how to draw."""
    arguments = tool_input if isinstance(tool_input, dict) else {}
    lowered = name.lower()
    command = arguments.get('command') or arguments.get('cmd')
    if isinstance(command, str) and command.strip():
        return command_execution(command)
    path = next(
        (str(arguments[key]) for key in _PATH_KEYS if str(arguments.get(key, '') or '').strip()),
        '',
    )
    if path and any(word in lowered for word in _EDIT_WORDS):
        return file_change([path])
    if 'search' in lowered and 'web' in lowered:
        return web_search(str(arguments.get('query', '') or ''))
    return tool_call(name)


def _result_event(raw: dict[str, Any]) -> dict[str, Any]:
    subtype = str(raw.get('subtype', '') or '')
    if not raw.get('is_error') and subtype in ('', GROK_RESULT_SUBTYPE_SUCCESS):
        usage = raw.get('usage')
        return turn_completed(usage if isinstance(usage, dict) else None)
    errors = [str(error).strip() for error in raw.get('errors') or () if str(error).strip()]
    reason = '; '.join(errors) or str(raw.get('result', '') or '').strip()
    return turn_failed(reason or _SUBTYPE_REASONS.get(subtype, f'the turn ended with {subtype}'))


_SUBTYPE_REASONS = {
    'error_max_turns': 'the agent used up its maximum number of turns',
    'error_during_execution': 'the turn failed while it ran',
}


# ----- argv -------------------------------------------------------------------

#: The output format this transport parses (see the module docstring).
OUTPUT_FORMAT = 'streaming-messages-json'
#: Grok's OS sandbox (Seatbelt on macOS, Landlock on Linux) has no Windows
#: implementation; there the workspace profile cannot be relied on.
SANDBOX_SUPPORTED = not sys.platform.startswith('win')
SANDBOX_WORKSPACE = 'workspace'
SANDBOX_READ_ONLY = 'read-only'
EFFORT_LEVELS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')


#: Every headless run: the parsed format, no self-update mid-task, and folder
#: trust — without it headless startup refuses a project that has rules or a
#: ``.grok`` config, which every repository with a CLAUDE.md is.
BASE_FLAGS = ('--output-format', OUTPUT_FORMAT, '--no-auto-update', '--trust')


def base_arguments(binary: str) -> list[str]:
    """``binary`` followed by every headless run's flags."""
    return [binary, *BASE_FLAGS]


def deny_rules(git_access: str = GIT_ACCESS_STRICT) -> list[str]:
    """The command floor as Grok deny rules. Deny beats every approval mode,
    ``--always-approve`` included, so these hold in every run."""
    arguments: list[str] = []
    for prefix in floor_command_prefixes(git_access):
        arguments.extend(['--deny', f'Bash({prefix}*)'])
    return arguments


def permission_arguments(*, sandbox: str = SANDBOX_WORKSPACE) -> list[str]:
    """How much the run may do without asking. A headless run cannot ask, so
    anything that would need approval is cancelled and the model told.

    * ``read-only``: the read-only sandbox, edits denied outright;
    * ``workspace``: approve every call INSIDE the workspace sandbox — the
      sandbox, not a prompt, is the boundary (the posture Codex's
      ``workspace-write`` has). Where the sandbox does not exist (Windows) the
      run auto-approves edits only, so a shell command is never approved with
      nothing around it.
    """
    if sandbox == SANDBOX_READ_ONLY:
        arguments = ['--deny', 'Edit', '--deny', 'Write']
        return (['--sandbox', SANDBOX_READ_ONLY] + arguments) if SANDBOX_SUPPORTED else arguments
    if SANDBOX_SUPPORTED:
        return ['--always-approve', '--sandbox', SANDBOX_WORKSPACE]
    return ['--permission-mode', 'acceptEdits']
