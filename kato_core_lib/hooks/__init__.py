"""Operator-extensible lifecycle hooks for kato.

Drop a JSON config at ``~/.kato/hooks.json`` (or pass an explicit
path via ``KATO_HOOKS_CONFIG``); kato reads it at boot and fires
the configured shell commands at the matching lifecycle points.

Six hook points are supported:

  - ``session_start``     — fires once when a streaming session spawns
  - ``session_end``       — fires once when the subprocess exits
  - ``pre_tool_use``      — fires before any tool invocation; exit code
                            non-zero blocks the tool
  - ``post_tool_use``     — fires after a tool runs; exit code is observed
                            for logging only
  - ``user_prompt_submit`` — fires when the operator submits a chat message
  - ``stop``              — fires when the operator clicks Stop

Config schema (illustrative):

    {
      "pre_tool_use": [
        {
          "match": {"tool": "Bash", "command_regex": "^rm -rf"},
          "command": "/usr/local/bin/block-dangerous-rm",
          "timeout_seconds": 5
        }
      ],
      "post_tool_use": [
        {"match": {"tool": "Edit"}, "command": "/usr/local/bin/lint ${file_path}"}
      ],
      "session_end": [
        {"command": "curl -X POST https://example/webhook -d '{\\"task\\":\\"${task_id}\\"}'"}
      ]
    }

The ``match`` block is optional — without it the hook fires for
every event at that lifecycle point. Substitution placeholders
(``${task_id}``, ``${tool}``, ``${file_path}``, etc.) are
replaced in the command string before exec. JSON of the full
event is also piped on stdin so hooks can parse it.

THIS MODULE IS NEW. It introduces a new operator-facing config
surface and is intentionally conservative: hooks are off-by-default
(no config file → no hooks), failures don't crash kato, and the
pre_tool_use ``block on exit != 0`` semantic is the only place
hooks affect kato's behaviour.
"""

# No re-export shims — callers import directly from the submodules:
#
#     from kato_core_lib.hooks.config import HookConfig, HookPoint, load_hooks_config
#     from kato_core_lib.hooks.runner import HookRunner
#
# Project convention (test_deployment_files.test_repo_does_not_use_all_export_shims)
# bans ``__all__`` at the package level.
