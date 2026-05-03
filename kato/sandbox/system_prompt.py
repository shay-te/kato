"""Sandbox-awareness system-prompt addendum.

When ``KATO_CLAUDE_DOCKER=true``, every Claude spawn appends a short
description of the sandboxed environment to the system prompt. The
addendum is purely informational for the agent — it does not change
any flag or capability — but it prevents the most common wasted-turn
loops:

  * trying to read the operator's host config files (``~/.aws/...``,
    ``/etc/...``) and getting confused error messages,
  * looping on ``apt-get install`` / ``pip install`` / ``curl`` calls
    that fail because egress is restricted to ``api.anthropic.com``,
  * attempting ``sudo`` or chown on paths outside the workspace mount.

The text is kept short (system prompts cost tokens on every turn) and
each claim is verifiable against ``kato.sandbox.manager.wrap_command``;
if a future change to the sandbox makes one of these false, the drift
guard test in ``tests/test_bypass_protections_doc_consistency.py``
will catch the divergence.

Independent of bypass mode: the *environment* is identical in
docker+bypass and docker-only mode, so the addendum describes what is
around the agent, not what the agent is allowed to do.
"""

from __future__ import annotations


SANDBOX_SYSTEM_PROMPT_ADDENDUM = (
    '# Sandboxed execution environment\n'
    '\n'
    'This Claude session is running inside the Kato hardened Docker sandbox.\n'
    'Your environment differs from a normal Claude Code run in three\n'
    'concrete ways:\n'
    '\n'
    "1. Filesystem. Your working directory is the operator's per-task\n"
    "   workspace. Files outside that directory are not the operator's\n"
    '   real files — paths like /Users/..., /home/..., or ~/.config\n'
    '   resolve to empty container scratch space, not the host.\n'
    '\n'
    '2. Network. Egress is restricted to api.anthropic.com only. Package\n'
    '   installs (apt-get, npm install, pip install), git clone against\n'
    '   external hosts, and arbitrary curl/wget calls will fail with a\n'
    '   connection error. These are not transient errors. Retrying them\n'
    '   will not help.\n'
    '\n'
    '3. Privileges. The container runs as a non-root user with all Linux\n'
    '   capabilities dropped. sudo is unavailable. Filesystem ownership\n'
    '   outside the workspace mount cannot be modified.\n'
    '\n'
    'This is your environment by design — the operator chose it. Work\n'
    'within it: read and edit the workspace files, use the language\n'
    "tooling that's already installed in the sandbox image, and do not\n"
    'waste turns attempting installs or external fetches. If a task\n'
    "genuinely requires a tool or network resource that isn't available,\n"
    'surface that to the operator in your reply. Do not work around it by\n'
    'attempting installs or fetches that will fail.\n'
)


def compose_system_prompt(
    architecture_doc: str,
    *,
    docker_mode_on: bool,
) -> str:
    """Combine the architecture doc and the sandbox addendum.

    The Claude CLI accepts a single ``--append-system-prompt`` value;
    when both pieces are present they are joined with a blank-line
    separator so the agent reads two distinct sections rather than a
    smushed paragraph. Either piece may be empty:

      * docker off, no architecture doc -> ``''``
      * docker off, architecture doc    -> the architecture doc verbatim
      * docker on,  no architecture doc -> the addendum verbatim
      * docker on,  architecture doc    -> ``arch + '\\n\\n' + addendum``

    Returning ``''`` when both are empty lets callers skip the
    ``--append-system-prompt`` flag entirely.
    """
    arch = architecture_doc or ''
    if docker_mode_on:
        if arch:
            return f'{arch}\n\n{SANDBOX_SYSTEM_PROMPT_ADDENDUM}'
        return SANDBOX_SYSTEM_PROMPT_ADDENDUM
    return arch
