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


# Repo-local config keys that make git EXECUTE a command. The agent has
# read-write access to the whole workspace clone — including ``.git`` —
# so every one of these is attacker-controlled input to a host process.
#
# ``core.hooksPath`` alone was NOT enough, and the gap was demonstrated,
# not theorised: with only the hook flag set, a ``core.fsmonitor`` line
# written into ``.git/config`` runs on the HOST the next time the caller issues
# ``git status`` on that clone — which it does routinely. Same for
# ``core.sshCommand`` on any fetch/push to an ssh remote.
#
# Command-line ``-c`` beats repo config, so overriding each key here
# neutralises it for every invocation built here at once.
_EXECUTION_CONFIG_OVERRIDES = (
    # Runs on status/diff/add — the single most reachable vector.
    'core.fsmonitor=false',
    # Runs on any ssh transport operation (fetch, push, ls-remote).
    'core.sshCommand=ssh',
    # Executed for git:// URLs.
    'core.gitProxy=',
    # Pagers and editors are spawned as shell commands.
    'core.pager=cat',
    'core.editor=true',
    'sequence.editor=true',
    # ``ext::<command>`` is a transport whose whole purpose is running a
    # command; a poisoned remote URL or insteadOf rewrite reaches it.
    'protocol.ext.allow=never',
    # Server-side hook honoured by some client operations.
    'uploadpack.packObjectsHook=',
    'core.alternateRefsCommand=',
    # A redirect is a credential-leak primitive: git would replay the
    # auth header to whatever host the redirect names. The caller only ever
    # talks to the provider URL it configured, so redirects are never
    # legitimate here.
    'http.followRedirects=false',
)


def build_safe_git_command(local_path: str, args: list[str]) -> list[str]:
    """``['git', ...hardening flags..., '-C', local_path, *args]`` —
    every git invocation in the codebase should be built through this,
    not by hand, so the hardening flags can never be forgotten.

    RESIDUALS, deliberately recorded rather than implied away:

    * Content filters (``filter.<name>.clean`` / ``.smudge``) cannot be
      disabled by a fixed ``-c`` list because the driver name is
      attacker-chosen, and they fire on checkout/add of a path that
      ``.gitattributes`` points at them.
    * ``diff.external`` is NOT overridden here. Setting it empty makes
      git run an empty external differ ("external diff died") and every
      patch comes back blank — a silent functional break, caught by the
      diff contract test. It is neutralised at the call site instead,
      with ``--no-ext-diff`` on the commands that generate patches.

    Closing the filter case needs the structural fix — keeping ``.git``
    out of the agent-writable mount, or running host git against a
    trusted ``--git-dir`` — not more flags here.
    """
    overrides: list[str] = []
    for setting in _EXECUTION_CONFIG_OVERRIDES:
        overrides.extend(['-c', setting])
    return [
        'git',
        *safe_directory_args(local_path),
        '-c', 'core.hooksPath=/dev/null',
        *overrides,
        '-C',
        local_path,
        *args,
    ]
