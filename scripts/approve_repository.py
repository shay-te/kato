#!/usr/bin/env python3
"""Operator-facing CLI for the Restricted Execution Protocol approval list.

Two ways to use it:

1. **Interactive mode** (no arguments). Lists every repo kato knows
   about — pulled from the kato config's ``repositories`` block AND
   from existing workspace clones — and offers a numbered picker
   plus an "approve all" option. The repository inventory is the
   primary source: it has the remote URL up front, so this works
   for **fresh tasks where no clone exists yet**, which is the
   common case (operator tags a YouTrack ticket, kato refuses on
   the REP gate, operator runs the picker before any clone has
   been created).

2. **Scripted mode** with explicit subcommands — kept for CI /
   automation that wants no interaction:

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


_OPTION_APPROVE_ALL = '[approve all listed repositories]'
_OPTION_QUIT = '[quit without approving anything]'


@dataclass(frozen=True)
class _DiscoveredRepository(object):
    """One repo the picker can offer for approval.

    ``source`` lets the picker render where the entry came from —
    ``inventory`` (kato config) vs ``workspace`` (existing clone) —
    so the operator can spot if the URL kato is about to record
    differs from what their kato config says. ``workspace_path``
    is empty for inventory-only entries (no clone yet).
    """

    repository_id: str
    remote_url: str
    source: str
    workspace_path: str = ''
    task_id: str = ''

    def render(self) -> str:
        bits = [self.repository_id]
        if self.source == 'workspace' and self.task_id:
            bits.append(f'({self.task_id})')
        bits.append(f'[{self.source}]')
        bits.append(f'→ {self.remote_url}')
        return '  '.join(bits)


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
    """``KATO_WORKSPACES_ROOT`` (or the default ``~/.kato/workspaces``)."""
    configured = os.environ.get('KATO_WORKSPACES_ROOT', '').strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / '.kato' / 'workspaces'


def _discover_inventory_repositories() -> list[_DiscoveredRepository]:
    """Read kato's ``repositories`` config block.

    This is the ground truth for "what repos can kato touch" — it has
    every repo's id and remote_url from the config, regardless of
    whether a clone exists on disk yet. For the fresh-task flow where
    kato just refused on REP and never cloned, the inventory is the
    only place we can get a remote URL from.

    Best-effort: if the config can't be loaded (kato not configured,
    Python path issues, dependency import failure), we return [] and
    the picker falls back to the workspace-clone scan + the
    "type a repo id manually" path.
    """
    try:
        # Lazy: importing the config machinery pulls in a lot of
        # kato. Operators running this on a half-set-up Windows box
        # would otherwise get a stack trace before the picker shows.
        from omegaconf import OmegaConf

        from kato_core_lib.data_layers.service.repository_inventory_service import (
            RepositoryInventoryService,
        )
    except Exception:
        return []
    config_path = _kato_config_path()
    if config_path is None or not config_path.is_file():
        return []
    try:
        cfg = OmegaConf.load(str(config_path))
        repositories_cfg = (
            getattr(getattr(cfg, 'kato', cfg), 'repositories', None)
            or getattr(cfg, 'repositories', None)
        )
        service = RepositoryInventoryService(repositories_cfg)
        repos = service.repositories
    except Exception:
        return []
    out: list[_DiscoveredRepository] = []
    seen: set[str] = set()
    for repo in repos:
        repo_id = str(getattr(repo, 'id', '') or '').strip()
        remote_url = str(getattr(repo, 'remote_url', '') or '').strip()
        if not repo_id or not remote_url:
            continue
        key = repo_id.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(_DiscoveredRepository(
            repository_id=repo_id,
            remote_url=remote_url,
            source='inventory',
        ))
    return sorted(out, key=lambda r: r.repository_id.lower())


def _kato_config_path() -> Path | None:
    """Locate the operator's kato config. None when nothing is set up."""
    configured = os.environ.get('KATO_CONFIG', '').strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_file() else None
    candidates = [
        Path.cwd() / '.kato' / 'kato.yaml',
        Path.cwd() / 'kato.yaml',
        Path.home() / '.kato' / 'kato.yaml',
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _discover_workspace_repositories(root: Path) -> list[_DiscoveredRepository]:
    """Walk ``<root>/<task_id>/<repo_id>/`` for already-cloned repos.

    Secondary source. Only useful after kato has run a task on this
    machine (the clone needs to exist). For fresh-task flows the
    inventory path above is what produces results.
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
            if repo_id.lower() in seen_ids:
                continue
            seen_ids.add(repo_id.lower())
            discovered.append(_DiscoveredRepository(
                repository_id=repo_id,
                remote_url=remote_url,
                source='workspace',
                workspace_path=str(repo_dir),
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


def _merge_sources(
    inventory: list[_DiscoveredRepository],
    workspace: list[_DiscoveredRepository],
) -> list[_DiscoveredRepository]:
    """Inventory wins on duplicates — its remote_url is the source of truth.

    A workspace clone might have a stale or operator-rewritten
    ``origin`` URL; the kato config's ``remote_url`` is what kato
    will actually use at runtime. So when both sources mention the
    same repo id, prefer the inventory entry.
    """
    by_id: dict[str, _DiscoveredRepository] = {}
    for repo in inventory:
        by_id[repo.repository_id.lower()] = repo
    for repo in workspace:
        by_id.setdefault(repo.repository_id.lower(), repo)
    return sorted(by_id.values(), key=lambda r: r.repository_id.lower())


def _filter_unapproved(
    repos: list[_DiscoveredRepository],
    service: RepositoryApprovalService,
) -> list[_DiscoveredRepository]:
    """Drop repos already approved with the SAME remote URL.

    Same remote = no-op approval = picker noise. Different remote
    stays in the list — operator needs to re-confirm.
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
    """List repos from inventory + workspace clones, prompt for a pick.

    The fresh-task flow this is built for:

      1. Operator tags a YouTrack ticket with ``kato:repo:<id>``.
      2. Kato refuses on the REP gate before any clone is created.
      3. Operator runs ``kato approve-repo`` (no args) — this picker.
      4. Inventory provides the remote URL (no clone needed).
      5. Operator picks the repo, kato writes the approval, retries.

    The picker also surfaces existing workspace clones so the
    "approve all repos I've already touched" use case still works.
    """
    inventory = _discover_inventory_repositories()
    workspace_root = _resolve_workspaces_root()
    workspace = _discover_workspace_repositories(workspace_root)
    candidates = _merge_sources(inventory, workspace)

    print(
        f'inventory: {len(inventory)} repo(s) from kato config; '
        f'workspaces: {len(workspace)} repo(s) under {workspace_root}',
    )
    if not candidates:
        print(
            'no repositories found. Add a ``repositories`` block to your '
            'kato config (or run a task to populate workspaces) and retry. '
            'You can also approve directly with: '
            'kato approve-repo approve <repo_id> --remote <git-url>',
            file=sys.stderr,
        )
        return 1

    service = RepositoryApprovalService()
    needing_decision = _filter_unapproved(candidates, service)
    if not needing_decision:
        print(
            f'every repository found is already approved with its current '
            f'remote URL. {len(candidates)} repo(s) in scope, all good. '
            f'Use ``kato list-approved-repos`` to inspect.',
        )
        return 0

    options = [repo.render() for repo in needing_decision]
    options.append(_OPTION_APPROVE_ALL)
    options.append(_OPTION_QUIT)

    selection = prompt_options(
        f'Pick a repository to approve ({len(needing_decision)} need a '
        f'decision), or approve all at once:',
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
        print(f'approving {len(needing_decision)} repository(ies)…')
        for repo in needing_decision:
            _approve_one(service, repo, trusted=trusted)
        return 0
    idx = options.index(selection)
    if idx >= len(needing_decision):
        print('unrecognised selection; aborting.', file=sys.stderr)
        return 1
    _approve_one(service, needing_decision[idx], trusted=trusted)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    # No subcommand → drop into the interactive picker. The scripted
    # form ``approve <id> --remote <url>`` is still available for CI.
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
