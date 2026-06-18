"""Force-approval settings for out-of-workspace file writes.

Out-of-folder writes are stopped by THREE layers; this module is the middle one:

1. **acceptEdits scope boundary (primary, comprehensive).** Claude Code only
   auto-accepts edits/filesystem commands for paths inside the working directory
   or ``--add-dir`` ``additionalDirectories`` (kato keeps that scope tight — just
   the task's repo clones under ``~/.kato/workspaces/<task>/``). Any write
   OUTSIDE that scope is routed to the permission prompt regardless of path, so
   the Action Guard + the operator decide. This already covers every absolute
   path, including the home directory.
2. **These ``permissions.ask`` rules (version-independent insurance).** A flat,
   enumerated denylist of roots that are NEVER the workspace, so an out-of-folder
   write to one of them is forced into the permission flow even if a CLI version
   ever regressed layer 1. The reported ``/tmp/strip_comments.py`` lands here.
3. **The post-hoc out-of-folder warning** (``_maybe_warn_out_of_sandbox_write``
   in ``session/streaming.py``) — a synthetic chat event for any write that
   still slipped through, so it is at least always visible.

The roots are ABSOLUTE and identical on the host and inside the Docker sandbox.
We deliberately do NOT enumerate the home directory here: the task workspace
lives under ``~/.kato/workspaces/…``, so a ``~/**`` rule would prompt on every
in-workspace edit and defeat ``acceptEdits``' whole purpose — and home writes
are already covered comprehensively by layer 1.
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
# ``/tmp/strip_comments.py`` lands under ``/tmp``; mounted volumes
# (``/Volumes``, ``/mnt``, ``/media``, ``/Network``) are classic exfil targets —
# an external/USB/network drive is never the task clone.
_OUT_OF_WORKSPACE_ROOTS: tuple[str, ...] = (
    '/tmp', '/private', '/var', '/etc', '/usr', '/opt',
    '/bin', '/sbin', '/dev', '/proc', '/sys', '/root',
    '/Library', '/System', '/Applications',
    '/Volumes', '/Network', '/mnt', '/media', '/srv',
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
