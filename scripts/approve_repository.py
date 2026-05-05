#!/usr/bin/env python3
"""Operator-facing CLI for the Restricted Execution Protocol approval list.

Two ways to use it:

1. **Interactive mode** (no arguments). Walks ``KATO_WORKSPACES_ROOT``
   for every cloned repo it can find, reads ``git remote get-url
   origin`` from each, and offers a numbered picker plus an
   "approve all" option. The operator just answers prompts; no need
   to remember repository ids or remote URLs.

2. **Scripted mode** with explicit subcommands — the original shape,
   kept for CI / automation that wants no interaction:

       approve <repo_id> --remote <git-url> [--trusted]
       revoke  <repo_id>
       list

Both paths defer to ``RepositoryApprovalService`` so semantics stay
co-located with the preflight gate.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from core_lib.helpers.command_line import prompt_options, prompt_yes_no

from kato_core_lib.data_layers.data.repository_approval import ApprovalMode
from kato_core_lib.data_layers.service.repository_approval_service import (
    RepositoryApprovalService,
)


# Sentinel options surfaced in the picker alongside the discovered
# repos. Strings (not enum) so ``prompt_options`` can render them
# directly in the same numbered list as the repos.
_OPTION_APPROVE_ALL = '[approve all discovered repositories]'
_OPTION_QUIT = '[quit without approving anything]'


@dataclass(frozen=True)
class _DiscoveredRepository(object):
    """One ``<task_id>/<repo_id>`` clone under ``KATO_WORKSPACES_ROOT``."""

    repository_id: str
    remote_url: str
    workspace_path: Path
    task_id: str

    def __str__(self) -> str:
        # Rendered in the picker; keep it scannable on a single
        # line. The remote URL is the load-bearing piece (it's what
        # gets recorded), so put it last for visual alignment.
        return f'{self.repository_id}  ({self.task_id})  →  {self.remote_url}'


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='kato approve-repo',
        description='Manage the Restricted Execution Protocol approval list.',
    )
    sub = parser.add_subparsers(dest='action', required=False)

    approve = sub.add_parser('approve', help='Approve a repository for kato use.')
    approve.add_argument('repository_id')
    approve.add_argument(
        '--remote', required=True,
        help='Git remote URL captured at approval time '
             '(URL changes force re-approval).',
    )
    approve.add_argument(
        '--trusted', action='store_true',
        help='Skip restricted mode; run with the operator global config.',
    )

    revoke = sub.add_parser('revoke', help='Remove an approval entry.')
    revoke.add_argument('repository_id')

    sub.add_parser('list', help='List repositories on the approval sidecar.')
    return parser


def _run_approve(args: argparse.Namespace) -> int:
    service = RepositoryApprovalService()
    mode = ApprovalMode.TRUSTED if args.trusted else ApprovalMode.RESTRICTED
    entry = service.approve(
        args.repository_id,
        args.remote,
        mode=mode,
    )
    print(
        f'approved {entry.repository_id!r} '
        f'(mode={entry.approval_mode.value}, '
        f'remote={entry.remote_url}, by={entry.approved_by})',
    )
    return 0


def _run_revoke(args: argparse.Namespace) -> int:
    service = RepositoryApprovalService()
    removed = service.revoke(args.repository_id)
    if removed:
        print(f'revoked approval for {args.repository_id!r}')
        return 0
    print(f'no approval on record for {args.repository_id!r}', file=sys.stderr)
    return 1


def _run_list(_args: argparse.Namespace) -> int:
    service = RepositoryApprovalService()
    rows = service.list_approvals()
    if not rows:
        print(f'no approvals on record at {service.storage_path}')
        return 0
    print(f'approvals at {service.storage_path}:')
    for entry in rows:
        print(
            f'  {entry.repository_id:<40} '
            f'{entry.approval_mode.value:<10} '
            f'{entry.remote_url}',
        )
    return 0


# ----- interactive mode -----


def _resolve_workspaces_root() -> Path:
    """Return ``KATO_WORKSPACES_ROOT`` (or the default ``~/.kato/workspaces``).

    Mirrors ``WorkspaceManager.from_config``'s precedence so the
    interactive picker walks the same directory kato itself writes
    into. We don't import the manager because that would pull in the
    full kato config machinery for what is fundamentally a directory
    walk.
    """
    configured = os.environ.get('KATO_WORKSPACES_ROOT', '').strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / '.kato' / 'workspaces'


def _discover_workspace_repositories(root: Path) -> list[_DiscoveredRepository]:
    """Walk ``<root>/<task_id>/<repo_id>/`` and return one entry per repo.

    A directory counts as a repository when it contains a ``.git``
    folder AND ``git remote get-url origin`` succeeds. Repos without
    a usable remote are skipped silently — they wouldn't be
    approvable anyway (the URL is the load-bearing field). Sorted
    by repository id for stable picker ordering.
    """
    if not root.is_dir():
        return []
    discovered: list[_DiscoveredRepository] = []
    seen_ids: set[str] = set()
    for task_dir in sorted(root.iterdir()):
        if not task_dir.is_dir():
            continue
        for repo_dir in sorted(task_dir.iterdir()):
            if not repo_dir.is_dir():
                continue
            if not (repo_dir / '.git').exists():
                continue
            remote_url = _read_origin_url(repo_dir)
            if not remote_url:
                continue
            repo_id = repo_dir.name
            # First-seen wins. Two tasks both touching the same repo
            # produce two clones with the same origin URL; we only
            # need to surface that repo once in the picker.
            if repo_id.lower() in seen_ids:
                continue
            seen_ids.add(repo_id.lower())
            discovered.append(_DiscoveredRepository(
                repository_id=repo_id,
                remote_url=remote_url,
                workspace_path=repo_dir,
                task_id=task_dir.name,
            ))
    return discovered


def _read_origin_url(repo_dir: Path) -> str:
    """Return ``git -C <repo_dir> remote get-url origin`` (empty on failure)."""
    try:
        result = subprocess.run(
            ['git', '-C', str(repo_dir), 'remote', 'get-url', 'origin'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ''
    if result.returncode != 0:
        return ''
    return result.stdout.strip()


def _filter_unapproved(
    repos: list[_DiscoveredRepository],
    service: RepositoryApprovalService,
) -> list[_DiscoveredRepository]:
    """Drop repos already on the approval sidecar with the same remote.

    The picker should only show repos that need a decision. A repo
    already approved with the SAME remote is a no-op — leaving it in
    the list is operator noise. A different remote means re-approval
    is needed (the URL changed under us), so it stays.
    """
    existing = {
        entry.repository_id.lower(): entry
        for entry in service.list_approvals()
    }
    out: list[_DiscoveredRepository] = []
    for repo in repos:
        approved = existing.get(repo.repository_id.lower())
        if approved is not None and approved.remote_url == repo.remote_url:
            continue
        out.append(repo)
    return out


def _approve_one(
    service: RepositoryApprovalService,
    repo: _DiscoveredRepository,
    *,
    trusted: bool,
) -> None:
    mode = ApprovalMode.TRUSTED if trusted else ApprovalMode.RESTRICTED
    entry = service.approve(repo.repository_id, repo.remote_url, mode=mode)
    print(
        f'  ✓ approved {entry.repository_id!r} '
        f'(mode={entry.approval_mode.value}, remote={entry.remote_url})',
    )


def _run_interactive() -> int:
    """Walk KATO_WORKSPACES_ROOT, list candidates, prompt for a pick.

    The operator sees:
        1. <repo-id-1>  (<task-id>)  →  <remote-url-1>
        2. <repo-id-2>  ...
        3. [approve all discovered repositories]
        4. [quit without approving anything]

    On a single repo pick: confirm trusted/restricted, write the
    approval. On "approve all": loop the same write per repo. On
    "quit": exit cleanly.
    """
    root = _resolve_workspaces_root()
    print(f'scanning {root} for cloned repositories…')
    discovered = _discover_workspace_repositories(root)
    if not discovered:
        print(
            f'no git repositories found under {root}. Run a kato task '
            'first (or set KATO_WORKSPACES_ROOT to where your clones '
            'live) and retry.',
            file=sys.stderr,
        )
        return 1

    service = RepositoryApprovalService()
    candidates = _filter_unapproved(discovered, service)
    if not candidates:
        print(
            f'every repository under {root} is already approved with '
            'its current remote URL. Nothing to do.',
        )
        return 0

    options = [str(repo) for repo in candidates]
    options.append(_OPTION_APPROVE_ALL)
    options.append(_OPTION_QUIT)

    selection = prompt_options(
        'Pick a repository to approve, or approve all at once:',
        options,
    )
    if selection == _OPTION_QUIT:
        print('quit; no approvals written.')
        return 0
    trusted = prompt_yes_no(
        'Approve in TRUSTED mode? '
        '(no — restricted, kato re-checks the remote URL at runtime; '
        'yes — trusted, runtime URL is not enforced)',
        default=False,
    )
    if selection == _OPTION_APPROVE_ALL:
        print(f'approving {len(candidates)} repository(ies)…')
        for repo in candidates:
            _approve_one(service, repo, trusted=trusted)
        return 0
    # Single-repo path: map the chosen string back to its dataclass.
    idx = options.index(selection)
    if idx >= len(candidates):  # defensive — shouldn't trip, sentinel
        print('unrecognised selection; aborting.', file=sys.stderr)
        return 1
    _approve_one(service, candidates[idx], trusted=trusted)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    # No subcommand given → drop into the interactive picker. The
    # operator can still get the full scripted shape with
    # ``approve <id> --remote <url>`` if they prefer.
    if args.action is None:
        return _run_interactive()
    if args.action == 'approve':
        return _run_approve(args)
    if args.action == 'revoke':
        return _run_revoke(args)
    if args.action == 'list':
        return _run_list(args)
    parser.error(f'unknown action: {args.action}')
    return 2


if __name__ == '__main__':
    sys.exit(main())
