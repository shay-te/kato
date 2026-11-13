"""OpenCode CLI wire protocol: ``opencode run --format json``.

Read from OpenCode's source (V2 ``packages/cli/src/run/noninteractive.ts``,
V1 ``packages/opencode/src/cli/cmd/run.ts``). Every stdout line is one event
in an envelope ``{type, timestamp, sessionID, …}``::

    {"type":"step_start","sessionID":"ses_…","part":{…}}
    {"type":"text","sessionID":"ses_…","part":{"type":"text","text":"…"}}
    {"type":"reasoning",…,"part":{"text":"…"}}            (only with --thinking)
    {"type":"tool_use",…,"part":{"tool":"bash","state":{"status":"completed",
        "input":{…},"output":"…"}}}
    {"type":"step_finish",…,"part":{"reason":"…","cost":…,"tokens":{…}}}
    {"type":"error",…,"error":{"type":"…","message":"…"}}        (V2)
    {"type":"error",…,"error":{"name":"…","data":{"message":"…"}}} (V1)

There is NO done event: a turn has succeeded when the process exits 0 with no
``error`` line (``PerTurnCliSession.CLEAN_EXIT_ENDS_TURN``). ``translate_event``
maps each line onto the shared per-turn vocabulary
(``agent_core_lib.session.turn_events``), the only shape the host draws.

The ARGV and ENVIRONMENT side lives here too, so the one-shot client and the
chat build a run the same way. OpenCode has no OS sandbox and no permission
flags: its rules live in config, which this transport passes inline in
``OPENCODE_CONFIG_CONTENT`` — edits allowed, the command floor denied, nothing
outside the project directory. With no terminal, an ``ask`` is auto-rejected
and the model told to continue without it.
"""

from __future__ import annotations

import json
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
    turn_failed,
    web_search,
)
from opencode_core_lib.opencode_core_lib.helpers.cli_generation import (
    GENERATION_V2,
    generation_flags,
)

OPENCODE_EVENT_STEP_START = 'step_start'
OPENCODE_EVENT_TEXT = 'text'
OPENCODE_EVENT_REASONING = 'reasoning'
OPENCODE_EVENT_TOOL_USE = 'tool_use'
OPENCODE_EVENT_STEP_FINISH = 'step_finish'
OPENCODE_EVENT_ERROR = 'error'

_EDIT_TOOLS = ('edit', 'write', 'patch', 'multiedit', 'apply_patch')
_PATH_KEYS = ('filePath', 'file_path', 'path')


def translate_event(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """One OpenCode line as events in the shared vocabulary (possibly none)."""
    kind = str(raw.get('type', '') or '')
    part = raw.get('part') if isinstance(raw.get('part'), dict) else {}
    if kind == OPENCODE_EVENT_STEP_START:
        session_id = str(raw.get('sessionID', '') or part.get('sessionID', '') or '').strip()
        return [session_started(session_id)] if session_id else []
    if kind == OPENCODE_EVENT_TEXT and str(part.get('text', '')).strip():
        return [agent_message(part['text'])]
    if kind == OPENCODE_EVENT_REASONING and str(part.get('text', '')).strip():
        return [reasoning(part['text'])]
    if kind == OPENCODE_EVENT_TOOL_USE:
        return [_tool_event(part)]
    if kind == OPENCODE_EVENT_ERROR:
        return [turn_failed(error_message(raw.get('error')))]
    # step_finish (token counts), retry, background_wait: nothing to show.
    return []


def _tool_event(part: dict[str, Any]) -> dict[str, Any]:
    tool = str(part.get('tool', '') or '')
    state = part.get('state') if isinstance(part.get('state'), dict) else {}
    arguments = state.get('input') if isinstance(state.get('input'), dict) else {}
    lowered = tool.lower()
    if lowered == 'bash' or isinstance(arguments.get('command'), str):
        output = state.get('output') if isinstance(state.get('output'), str) else ''
        return command_execution(str(arguments.get('command', '') or ''), output=output)
    path = next(
        (str(arguments[key]) for key in _PATH_KEYS if str(arguments.get(key, '') or '').strip()),
        '',
    )
    if lowered in _EDIT_TOOLS and path:
        return file_change([path])
    if lowered in ('websearch', 'webfetch'):
        return web_search(str(arguments.get('query', '') or arguments.get('url', '') or ''))
    return tool_call(tool)


def error_message(error: Any) -> str:
    """The readable text of an ``error`` line, from either generation's shape."""
    if isinstance(error, str):
        return error.strip()
    if not isinstance(error, dict):
        return 'the turn failed'
    message = error.get('message')
    data = error.get('data') if isinstance(error.get('data'), dict) else {}
    for candidate in (message, data.get('message'), error.get('name'), error.get('type')):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return 'the turn failed'


# ----- argv + environment -------------------------------------------------------

RUN_ARGUMENTS = ('run', '--format', 'json')
CONFIG_ENV_KEY = 'OPENCODE_CONFIG_CONTENT'
#: Read-only runs (a triage, an independent review): reading only.
MODE_READ_ONLY = 'read-only'
#: Every other run: edit inside the project, the command floor denied.
MODE_WORKSPACE = 'workspace'
EFFORT_LEVELS = ('minimal', 'low', 'medium', 'high', 'max')


def run_arguments(binary: list[str] | tuple[str, ...], generation: int) -> list[str]:
    """``<binary> run --format json`` plus what this generation needs."""
    return [*binary, *RUN_ARGUMENTS, *generation_flags(generation)]


def model_arguments(model: str, effort: str, generation: int) -> list[str]:
    """The model, and the effort as its variant — the spelling differs per
    generation, and a V2 variant rides ON the model id, so with no model
    named there is nowhere to put it."""
    if generation >= GENERATION_V2:
        if not model:
            return []
        return ['-m', f'{model}#{effort}' if effort else model]
    arguments = ['-m', model] if model else []
    if effort:
        arguments.extend(['--variant', effort])
    return arguments


def permission_config(git_access: str = GIT_ACCESS_STRICT, *, mode: str = MODE_WORKSPACE) -> dict:
    """The run's permission rules, in the map form both generations accept.

    The last matching rule wins, so each map opens with its catch-all.
    """
    if mode == MODE_READ_ONLY:
        return {'permission': {
            'edit': 'deny',
            'bash': 'deny',
            'webfetch': 'deny',
            'external_directory': 'deny',
        }}
    shell: dict[str, str] = {'*': 'allow'}
    for prefix in floor_command_prefixes(git_access):
        shell[prefix] = 'deny'
        shell[f'{prefix} *'] = 'deny'
    return {'permission': {
        'edit': 'allow',
        'bash': shell,
        'webfetch': 'allow',
        'external_directory': 'deny',
    }}


def run_environment(
    project_dir: str,
    *,
    git_access: str = GIT_ACCESS_STRICT,
    mode: str = MODE_WORKSPACE,
) -> dict[str, str]:
    """Variables a run adds to the host's environment.

    ``PWD`` because OpenCode takes its project directory from ``$PWD`` BEFORE
    the process's own working directory (V2 has no directory flag at all).
    """
    variables = {
        CONFIG_ENV_KEY: json.dumps(permission_config(git_access, mode=mode)),
        'OPENCODE_DISABLE_AUTOUPDATE': '1',
    }
    if project_dir:
        variables['PWD'] = project_dir
    return variables
