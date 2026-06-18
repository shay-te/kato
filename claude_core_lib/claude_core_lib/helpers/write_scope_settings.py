"""Force-approval settings for out-of-workspace file writes.

The Claude CLI's ``acceptEdits`` mode auto-accepts file edits — INCLUDING to
scratch paths like ``/tmp`` — without routing them through kato's permission
path, so an out-of-task write slipped by with only a post-hoc warning and no
approval. Injecting ``permissions.ask`` rules for the file-write tools on
system/scratch roots forces those writes back into the permission flow (where
the Action Guard ``out_of_scope`` rule + the operator's approval modal decide),
even under ``acceptEdits``. The post-hoc out-of-folder warning stays as the
comprehensive backstop for any path these rules don't enumerate.

The roots are ABSOLUTE and identical on the host and inside the Docker sandbox.
We deliberately do NOT include the home directory: the task workspace lives
under ``~/.kato/workspaces/…``, so a ``~/**`` rule would prompt on every
in-workspace edit and defeat ``acceptEdits``' whole purpose.
"""
from __future__ import annotations

import json

# File-mutating tools that ``acceptEdits`` auto-accepts. Bash is NOT here: it
# already routes through the permission callback under ``acceptEdits``, so an
# out-of-folder ``bash`` write is already gated by kato.
_WRITE_TOOLS: tuple[str, ...] = ('Write', 'Edit', 'MultiEdit', 'NotebookEdit')

# Absolute roots that are NEVER the task workspace — a write here is always
# out-of-folder and must be approved, not auto-accepted. Same paths on the host
# and in the container (so the rule holds in both run modes). The reported
# ``/tmp/strip_comments.py`` lands under ``/tmp``.
_OUT_OF_WORKSPACE_ROOTS: tuple[str, ...] = (
    '/tmp', '/private', '/var', '/etc', '/usr', '/opt',
    '/bin', '/sbin', '/dev', '/proc', '/sys', '/root',
    '/Library', '/System', '/Applications',
)


def out_of_workspace_write_ask_rules() -> list[str]:
    """``Tool(root/**)`` ask-rules for every write-tool × out-of-workspace root.

    e.g. ``Write(/tmp/**)``, ``Edit(/etc/**)`` — matched against the target
    file path; an in-workspace edit (under the task clone) matches none of
    these, so it still flows through ``acceptEdits`` untouched.
    """
    return [
        f'{tool}({root}/**)'
        for tool in _WRITE_TOOLS
        for root in _OUT_OF_WORKSPACE_ROOTS
    ]


def out_of_workspace_write_settings() -> dict:
    """Settings dict that forces approval for out-of-workspace file writes."""
    return {'permissions': {'ask': out_of_workspace_write_ask_rules()}}


def out_of_workspace_write_settings_json() -> str:
    """The settings as a compact JSON string for ``claude --settings``."""
    return json.dumps(out_of_workspace_write_settings(), separators=(',', ':'))
