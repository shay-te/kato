"""Shared hardening for every git subprocess invocation in the codebase.

``core.hooksPath=/dev/null`` disables every git hook for our own
invocations — guards against a sandboxed coding agent dropping a
malicious hook (``post-checkout``, ``pre-push``, ...) in a per-task
workspace clone that would otherwise fire with the OPERATOR'S own
OS-user privileges the next time anything in the host application runs
a git command in that clone (checkout, push, commit, ...) — a real RCE-out-of-sandbox
path if any git-invoking code forgets this flag.

This lives here (not inlined per call site) specifically so there is
only ONE place to get right: a second, independently-written git
helper that duplicated this flag inline had drifted and shipped
without it, reopening exactly the hole this guards against.
"""
from __future__ import annotations

from utils_core_lib.utils_core_lib.text_utils import normalized_text


def safe_directory_args(local_path: str) -> list[str]:
    safe_directory = normalized_text(local_path)
    if not safe_directory:
        return []
    return ['-c', f'safe.directory={safe_directory}']


def build_safe_git_command(local_path: str, args: list[str]) -> list[str]:
    """``['git', ...hardening flags..., '-C', local_path, *args]`` —
    every git invocation in the codebase should be built through this,
    not by hand, so the hook-disabling flag can never be forgotten."""
    return [
        'git',
        *safe_directory_args(local_path),
        '-c', 'core.hooksPath=/dev/null',
        '-C',
        local_path,
        *args,
    ]
