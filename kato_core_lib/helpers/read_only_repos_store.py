"""Persistent per-task set of repository ids kato must NOT push to.

A task can touch repos kato can't push (external reference libraries returning a
403, or repos the operator marked with a ``kato:repo-ref:`` tag). The preflight
push-access check partitions them out so one un-pushable repo doesn't reject the
whole task; this store remembers the decision so:

  * the publish step skips push/PR for them,
  * the planning UI's file tree can badge them "read-only", and
  * a "re-check" action can drop a repo once push access is granted.

Stored as ``{task_id: [repo_id, ...]}`` at ``~/.kato/read_only_repos.json``
(override via ``KATO_READ_ONLY_REPOS_PATH``). Task ids are matched
case-insensitively (the platform yields ``UNA-1`` while records are lowercased)
— mirrors the policy in ``forgotten_tasks_store``.
"""
from __future__ import annotations

import json
from pathlib import Path

from kato_core_lib.helpers.atomic_json_utils import atomic_write_json
from kato_core_lib.helpers.kato_paths_utils import kato_home_path

_ENV_KEY = 'KATO_READ_ONLY_REPOS_PATH'
_FILENAME = 'read_only_repos.json'


def _path() -> Path:
    return kato_home_path(_FILENAME, env_key=_ENV_KEY)


def _norm_task(task_id: object) -> str:
    return str(task_id or '').strip().lower()


def _norm_repo(repo_id: object) -> str:
    return str(repo_id or '').strip()


def _read_all() -> dict[str, list[str]]:
    path = _path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, value in data.items():
        if not isinstance(value, list):
            continue
        repos = [_norm_repo(item) for item in value if _norm_repo(item)]
        if repos:
            out[_norm_task(key)] = repos
    return out


def _write_all(data: dict[str, list[str]]) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    # Sort for stable diffs; drop empty entries.
    cleaned = {k: sorted(set(v)) for k, v in data.items() if v}
    atomic_write_json(path, dict(sorted(cleaned.items())))


def read_only_repos(task_id: str) -> set[str]:
    """Repo ids marked read-only for ``task_id`` (empty set if none)."""
    return set(_read_all().get(_norm_task(task_id), []))


def is_read_only(task_id: str, repo_id: str) -> bool:
    repo = _norm_repo(repo_id)
    return bool(repo) and repo in read_only_repos(task_id)


def set_read_only_repos(task_id: str, repo_ids) -> None:
    """Replace the read-only set for ``task_id`` (clears it when empty)."""
    task = _norm_task(task_id)
    if not task:
        return
    repos = sorted({_norm_repo(r) for r in (repo_ids or []) if _norm_repo(r)})
    data = _read_all()
    if repos:
        data[task] = repos
    else:
        data.pop(task, None)
    _write_all(data)


def clear_read_only_repo(task_id: str, repo_id: str) -> None:
    """Drop a single repo from a task's read-only set (e.g. push now works)."""
    task = _norm_task(task_id)
    repo = _norm_repo(repo_id)
    if not task or not repo:
        return
    data = _read_all()
    remaining = [r for r in data.get(task, []) if r != repo]
    if remaining:
        data[task] = remaining
    else:
        data.pop(task, None)
    _write_all(data)


def forget_task(task_id: str) -> None:
    """Drop every read-only entry for a task (used when the task is forgotten)."""
    task = _norm_task(task_id)
    if not task:
        return
    data = _read_all()
    if data.pop(task, None) is not None:
        _write_all(data)
