"""Shared workspace repo-path helpers.

One source of truth for "which on-disk repo clones does a task have, and
which should Claude get as ``--add-dir``s beside its ``cwd``". Both the
chat-send route and the comment-run respawn need this; keeping it here
stops the two from drifting (and stops a comment-driven respawn from
spawning a single-repo session that can't reach the task's other repos —
the cross-repo "that repo is forbidden" refusal).
"""

from __future__ import annotations

from pathlib import Path


def task_workspace_root(workspace_manager, task_id: str) -> str:
    """The task's own folder — parent of every repo clone for this task.

    Empty when the workspace manager can't produce one or the directory
    doesn't exist. Never derived from a repo path by walking upward: an
    adopted checkout's parent could be the operator's entire source root,
    and handing THAT out as a scope boundary (or bind-mounting it) would be
    strictly worse than the per-repo scope it replaced.

    Lives here rather than in the webserver because both spawn paths need
    the same answer: the chat-send route and the ``kato:wait-planning`` /
    ``kato:wait-editing`` hold spawn. The hold spawn had no equivalent at
    all, which is why a wait-editing session opened with no task-folder
    boundary in its prompt and no sandbox root — the agent could not find
    its own workspace and asked the operator for the clone directory.
    """
    if workspace_manager is None or not task_id:
        return ''
    try:
        path = workspace_manager.workspace_path(task_id)
    except Exception:
        return ''
    text = str(path or '')
    if not text:
        return ''
    try:
        return text if Path(text).is_dir() else ''
    except OSError:
        return ''


def _is_inside(candidate: str, root: str) -> bool:
    """Is ``candidate`` the same directory as ``root`` or below it?

    Resolves both so a symlinked workspaces root (``/tmp`` → ``/private/tmp``
    on macOS) or a ``..`` segment can't make a path that IS inside look like
    one that isn't.
    """
    try:
        return Path(candidate).expanduser().resolve().is_relative_to(
            Path(root).expanduser().resolve(),
        )
    except (OSError, ValueError):
        return False


def task_repository_clones(workspace_manager, task_id: str) -> list[str]:
    """The task's repo clone directories that actually exist on disk."""
    if workspace_manager is None or not task_id:
        return []
    try:
        workspace = workspace_manager.get(task_id)
    except Exception:
        return []
    if workspace is None:
        return []
    clones: list[str] = []
    for repository_id in (getattr(workspace, 'repository_ids', None) or []):
        try:
            path = workspace_manager.repository_path(task_id, str(repository_id))
        except Exception:
            continue
        try:
            if path and Path(path).is_dir():
                clones.append(str(path))
        except OSError:
            continue
    return clones


def resolve_session_cwd(workspace_manager, task_id: str, candidate_cwd: str = '') -> str:
    """The directory a session for ``task_id`` is allowed to spawn in.

    A task that HAS a workspace may only ever run inside it. ``candidate_cwd``
    — normally the ``cwd`` persisted on the session record — is honoured only
    when it is inside that workspace; anything else is replaced with the
    task's own first repo clone.

    This exists because a bad ``cwd`` used to be self-perpetuating. A session
    spawned with no cwd took the ORCHESTRATOR'S working directory (kato's own
    checkout), that value was written to the session record, and every later
    respawn read the record back and started there again — so a ticket whose
    workspace was ``<workspaces>/<TASK>/<repo>`` ran the agent inside kato's
    sources, with the sandbox scope derived from the same wrong path. The
    spawn-side hole is closed (a streaming session now refuses an empty cwd),
    and this closes the read side: records already poisoned by the old
    behaviour are corrected on the way out, with no migration.

    Deliberately NOT the bare task folder when clones exist: sandbox scoping
    widens one level up from ``cwd`` on the assumption that ``cwd`` is
    ``<workspaces>/<task>/<repo>``, so handing back ``<workspaces>/<task>``
    would widen the sandbox to the workspaces root — every other task's
    checkout. The folder is used only when the task genuinely has no clone
    yet, where it is the narrowest true answer.

    A task with NO workspace (adopted-cwd checkouts, legacy single-clone
    installs) keeps ``candidate_cwd`` untouched: there is nothing to validate
    against, and inventing a boundary would be worse than trusting the
    operator's own directory.
    """
    candidate = str(candidate_cwd or '').strip()
    root = task_workspace_root(workspace_manager, task_id)
    if not root:
        return candidate
    if candidate and _is_inside(candidate, root):
        return candidate
    clones = task_repository_clones(workspace_manager, task_id)
    return clones[0] if clones else root


def sibling_repository_dirs(workspace_manager, task_id: str) -> list[str]:
    """The task's whole workspace folder, for ``--add-dir`` beyond ``cwd``.

    Workspace mode: returns ``<workspace_root>/<task_id>`` — the single
    parent folder every one of the task's repo clones lives under —
    rather than enumerating each currently-known ``repository_id``.
    The operator can attach another repo to the task mid-conversation;
    it clones into this SAME folder. A session's ``--add-dir`` set is
    baked in at spawn time and never widened later, so an enumerated
    repo list would miss a repo added after spawn until the session
    is respawned — scoping to the whole task folder covers it
    immediately (the "attach a new repo and kato still can't see it"
    bug). Empty list when there's no workspace (e.g. adopted-cwd tasks
    pointing at the dev's own checkout); we never probe parent dirs
    blindly.
    """
    if workspace_manager is None or not task_id:
        return []
    try:
        workspace = workspace_manager.get(task_id)
    except Exception:
        return []
    if workspace is None:
        return []
    try:
        task_root = str(workspace_manager.workspace_path(task_id))
    except Exception:
        return []
    if not task_root:
        return []
    return [task_root]
