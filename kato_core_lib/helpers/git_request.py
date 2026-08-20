"""Agent → kato git requests: the agent ASKS, kato performs the operation.

Some git operations are kato's, not the agent's: kato owns the branch state
machine and the publish path, so ``commit``/``push``/``checkout`` are denied
to the agent at the transport floor. That is correct, but on its own it
leaves the agent stuck — it hits the wall and reports "git is forbidden",
which is what an operator sees as kato being broken.

This is the other half: a channel for the agent to say WHAT it needs and
WHY, so kato can do it under its own hardened git client (hooks disabled,
remote pinned, argv validated) instead of the agent doing it unsupervised.

TRANSPORT: a JSON file in the task workspace, not an HTTP endpoint. The
sandbox joins a private ``--internal`` network whose only route out is an
SNI proxy pinned to the model API — a localhost call to kato's webserver
cannot leave the container at all. The workspace is bind-mounted, so a file
works identically in sandboxed and host modes, with no new network reach.

APPROVAL IS NOT OPTIONAL for anything that publishes or moves the branch.
The agent asking does not make it automatic: kato never commits, pushes, or
opens a pull request without an operator action, and routing that through a
request file would be the same rule broken by a different door. Requests are
classified here; the caller enforces.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

#: File the agent writes to ask for a git operation.
GIT_REQUEST_FILENAME = 'git_request.json'
#: File kato writes back so the agent can read the outcome.
GIT_RESULT_FILENAME = 'git_result.json'

#: Operations kato will perform on request, and whether the operator has to
#: approve first.
#:
#: ``False`` here means "kato may just do it": the operation neither
#: publishes nor moves the branch, so an operator prompt would be noise.
#: Everything that changes what gets shipped, or where HEAD points, is
#: ``True`` — the agent's request is an argument for doing it, never the
#: decision to do it.
REQUIRES_APPROVAL: dict[str, bool] = {
    'stage': False,          # git add — kato stages everything at publish anyway
    'unstage': False,        # git restore --staged
    'commit': True,          # what gets shipped
    'create_branch': True,   # branch state
    'switch_branch': True,   # branch state, and it moves the agent's worktree
    'clean': True,           # deletes untracked files, unrecoverably
}

#: Never performed on an agent's request, whatever it says. ``push`` and
#: ``open_pull_request`` are the operator's Done button by design — that is
#: the whole "kato never publishes on its own" guarantee, and a request file
#: is not a loophole in it. ``config`` is the hook/RCE surface.
REFUSED: dict[str, str] = {
    'push': (
        'kato never pushes on its own — publishing is the operator\'s '
        'decision. Finish your work and say so; the operator publishes with '
        'the Done button.'
    ),
    'open_pull_request': (
        'kato never opens a pull request on its own — the operator publishes '
        'with the Done button once they are satisfied with the work.'
    ),
    'config': (
        'git config is refused: it is an arbitrary-code-execution surface '
        '(core.fsmonitor, core.sshCommand, url.*.insteadOf all run commands '
        'on ordinary git operations), so nothing sets it on request.'
    ),
    'reset': (
        'git reset is refused: it moves the branch and can discard the whole '
        'task\'s uncommitted work at once. To undo file content, use '
        '"git restore --source=<commit> -- <path>", which you can run '
        'yourself.'
    ),
}


class GitRequestError(ValueError):
    """The request file is unusable. The message is shown to the agent."""


@dataclass(frozen=True)
class GitRequest(object):
    """One validated request from the agent."""

    operation: str
    repository_id: str
    reason: str
    paths: tuple
    message: str
    branch: str

    @property
    def needs_approval(self) -> bool:
        return REQUIRES_APPROVAL.get(self.operation, True)


def request_path(workspace_path: str) -> str:
    return os.path.join(str(workspace_path or ''), GIT_REQUEST_FILENAME)


def result_path(workspace_path: str) -> str:
    return os.path.join(str(workspace_path or ''), GIT_RESULT_FILENAME)


def _text(value: object) -> str:
    return str(value or '').strip()


def parse_request(raw: object) -> GitRequest:
    """Validate one request payload into a :class:`GitRequest`.

    Raises :class:`GitRequestError` with a message written FOR THE AGENT —
    it is handed back through ``git_result.json``, so it has to say what to
    do differently rather than just what was wrong.
    """
    if not isinstance(raw, dict):
        raise GitRequestError(
            'git_request.json must contain a JSON object with an '
            '"operation" field.',
        )
    operation = _text(raw.get('operation')).lower().replace('-', '_')
    if not operation:
        raise GitRequestError(
            'git_request.json needs an "operation" field. Supported: '
            + ', '.join(sorted(REQUIRES_APPROVAL)),
        )
    if operation in REFUSED:
        raise GitRequestError(REFUSED[operation])
    if operation not in REQUIRES_APPROVAL:
        raise GitRequestError(
            f'unknown git operation {operation!r}. Supported: '
            + ', '.join(sorted(REQUIRES_APPROVAL)),
        )
    reason = _text(raw.get('reason'))
    if not reason:
        # The operator sees this and nothing else when deciding. A request
        # with no stated reason is one they cannot judge, so it is rejected
        # here rather than surfaced as an unanswerable prompt.
        raise GitRequestError(
            'git_request.json needs a "reason" explaining why you need this '
            'operation — the operator decides based on it.',
        )
    paths = tuple(
        _text(entry) for entry in (raw.get('paths') or []) if _text(entry)
    )
    return GitRequest(
        operation=operation,
        repository_id=_text(raw.get('repository_id') or raw.get('repo_id')),
        reason=reason,
        paths=paths,
        message=_text(raw.get('message')),
        branch=_text(raw.get('branch')),
    )


def read_request(workspace_path: str):
    """Read + validate the request file. ``None`` when there is no request.

    A malformed file raises so the caller can answer the agent; an ABSENT
    file is the normal case and is not an error.
    """
    path = request_path(workspace_path)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8') as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        raise GitRequestError(
            f'git_request.json could not be read as JSON ({exc}). Write a '
            'single JSON object, e.g. {"operation": "commit", "reason": '
            '"..."}.',
        ) from exc
    return parse_request(raw)


def clear_request(workspace_path: str) -> None:
    """Remove the request file once handled.

    Best-effort: a leftover file would be re-read forever, but failing to
    delete it must not fail the operation that already ran.
    """
    try:
        os.remove(request_path(workspace_path))
    except OSError:
        pass


def write_result(workspace_path: str, *, ok: bool, detail: str, operation: str = '') -> None:
    """Answer the agent in ``git_result.json``.

    Always written, including on refusal — an agent that asked and heard
    nothing back has no way to tell "not handled yet" from "refused", and
    will either hang or retry forever.
    """
    payload = {'ok': bool(ok), 'operation': operation, 'detail': detail}
    try:
        with open(result_path(workspace_path), 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, indent=2)
    except OSError:
        pass


def agent_guidance_text() -> str:
    """The block telling the agent this channel exists and how to use it.

    Injected into the prompt by the orchestrator. Without it the agent has
    no way to know the channel exists, and goes on reporting a hard refusal
    for something kato would happily do.
    """
    operations = ', '.join(sorted(REQUIRES_APPROVAL))
    return (
        'If you need a git operation that kato owns (committing, creating or '
        'switching a branch, staging, cleaning), do NOT try to run it and do '
        'NOT report yourself as blocked. Ask kato to do it: write '
        f'{GIT_REQUEST_FILENAME} in the TASK folder (the parent of the '
        'repository clones) with a JSON object like '
        '{"operation": "commit", "repository_id": "<repo>", "reason": '
        '"<why you need it>", "message": "<commit message>"}. '
        f'Supported operations: {operations}. '
        'kato performs it with its own git client and writes the outcome to '
        f'{GIT_RESULT_FILENAME}; read that file to see whether it succeeded. '
        'Anything that changes the branch or what gets shipped is shown to '
        'the operator first, so state the reason clearly — that is what they '
        'decide on. '
        'Pushing and opening the pull request are NOT available through this '
        'channel at all: the operator publishes with the Done button when '
        'they are satisfied. Say your work is ready and stop there.'
    )
