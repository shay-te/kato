"""Hardened Docker sandbox for the Claude CLI.

Activated by ``KATO_CLAUDE_BYPASS_PERMISSIONS=true`` — when bypass is on,
``--permission-mode bypassPermissions`` lets Claude run arbitrary shell
commands without asking. The sandbox bounds what those commands can
actually do: only the per-task workspace folder is mounted, the
container's egress firewall blocks everything except ``api.anthropic.
com``, and Claude runs as a non-root user with no capabilities.

See :mod:`kato.sandbox.manager` for the public entry points used by
the spawn path and the startup preflight.
"""
