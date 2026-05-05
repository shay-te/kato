#!/usr/bin/env python3
"""Operator-facing CLI for the Restricted Execution Protocol approval list.

**Interactive (no arguments)** — one picker for all operations.
Shows every repo kato can find, with a ``[x]`` next to the ones
already approved. The operator types a comma-separated list of
indices to toggle: anything you check that wasn't approved becomes
approved; anything you uncheck that was approved is revoked. One
command, one screen, add+edit+remove in a single Apply step. No
sub-modes for the operator to remember.

Sources scanned for repo discovery:

1. The kato config's ``repositories`` block (canonical: id +
   remote_url straight from config). Works for fresh tasks where
   no clone exists yet.
2. ``KATO_WORKSPACES_ROOT/<task>/<repo>/.git`` — kato's per-task
   clones. Useful after kato has actually run something.
3. ``REPOSITORY_ROOT_PATH/<repo>/.git`` — the operator's local
   checkout root (the same folder kato pushes branches to at task
   end). This is what unblocks the case Shubham hit in #ops: kato
   refused on REP, no workspace clone existed yet, but the repo
   was sitting right there in his ``REPOSITORY_ROOT_PATH``.

When NO source can be located (no kato config, no workspaces, no
``REPOSITORY_ROOT_PATH``), we exit with a precise message naming
which env vars are missing and how to set them — instead of
silently showing "0 repositories found".

**Scripted mode** is unchanged and still supported for CI:

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

from core_lib.helpers.command_line import prompt_yes_no

from kato_core_lib.data_layers.data.repository_approval import ApprovalMode
from kato_core_lib.data_layers.service.repository_approval_service import (
    RepositoryApprovalService,
)


def _bootstrap_env_from_dotenv() -> None:
    """Load ``<repo>/.env`` into ``os.environ`` if not already loaded.

    The ``tools/kato/kato.py`` dispatcher does this before invoking
    us, but a developer running ``python scripts/approve_repository.py``
    bypasses the dispatcher and would otherwise see a bare environment
    on Windows (where the operator's shell almost never carries
    kato's vars). Real env vars still win — see the same loader in
    the dispatcher for the reasoning.
    """
    repo_root = Path(__file__).resolve().parents[1]
    env_path = repo_root / '.env'
    if not env_path.is_file():
        return
    try:
        text = env_path.read_text(encoding='utf-8')
    except OSError:
        return
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        if '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        os.environ[key] = value


_bootstrap_env_from_dotenv()


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


def _resolve_repository_root() -> Path | None:
    """``REPOSITORY_ROOT_PATH`` from env. None when unset.

    Loaded by ``tools/kato/kato.py`` from ``<kato>/.env`` before this
    script runs, so an operator who edited ``.env`` (the standard
    config file) sees the value here without having to re-export it
    in their shell. A real shell variable still wins — see the
    ``.env`` loader in the dispatcher.
    """
    configured = os.environ.get('REPOSITORY_ROOT_PATH', '').strip()
    if not configured:
        return None
    return Path(configured).expanduser()


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


def _discover_repository_root_repositories(root: Path) -> list[_DiscoveredRepository]:
    """Walk ``<REPOSITORY_ROOT_PATH>/<repo>/`` for the operator's
    own local clones.

    This is the fresh-task source that was missing: kato refuses on
    the REP gate before any per-task clone exists, but the repo
    almost always already lives in the operator's checkout root
    (the same folder kato pushes branches into at task end). Walking
    one level deep finds it without needing the kato config to
    declare every repo up front.

    Top-level layout only (``<root>/<repo>/.git``). We don't recurse
    — operators sometimes nest experiments under the root and we
    don't want to surface every sub-clone they have lying around.
    """
    if not root.is_dir():
        return []
    discovered: list[_DiscoveredRepository] = []
    seen_ids: set[str] = set()
    for repo_dir in sorted(root.iterdir()):
        if not repo_dir.is_dir():
            continue
        if not (repo_dir / '.git').exists():
            continue
        remote_url = _read_origin_url(repo_dir)
        if not remote_url:
            continue
        repo_id = repo_dir.name
        key = repo_id.lower()
        if key in seen_ids:
            continue
        seen_ids.add(key)
        discovered.append(_DiscoveredRepository(
            repository_id=repo_id,
            remote_url=remote_url,
            source='checkout',
            workspace_path=str(repo_dir),
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
    *source_lists: list[_DiscoveredRepository],
) -> list[_DiscoveredRepository]:
    """First source wins on duplicates. Pass sources in priority order.

    Inventory wins because its ``remote_url`` is the value kato will
    actually use at runtime (a clone's ``origin`` URL might have
    been operator-rewritten). REPOSITORY_ROOT_PATH wins over kato
    workspace clones because the operator's checkout root is what
    they actually pushed/pulled and is more likely to be the
    canonical remote URL.
    """
    by_id: dict[str, _DiscoveredRepository] = {}
    for source_list in source_lists:
        for repo in source_list:
            by_id.setdefault(repo.repository_id.lower(), repo)
    return sorted(by_id.values(), key=lambda r: r.repository_id.lower())


@dataclass
class _Row(object):
    """One line in the unified picker.

    ``initially_approved`` reflects what's on the approval list when
    the picker boots; ``selected`` reflects the operator's edits.
    The Apply pass diff'd the two to compute add/revoke operations.
    """

    repo: _DiscoveredRepository
    initially_approved: bool
    initial_remote: str
    initial_mode: str
    selected: bool

    @property
    def changed(self) -> bool:
        """True when this row would produce a write on Apply."""
        if self.selected and not self.initially_approved:
            return True
        if not self.selected and self.initially_approved:
            return True
        if (
            self.selected
            and self.initially_approved
            and self.initial_remote != self.repo.remote_url
        ):
            # Same id, different remote — this is the "URL changed,
            # re-approve" case. We want it to count as a change so
            # the operator notices.
            return True
        return False


def _build_rows(
    candidates: list[_DiscoveredRepository],
    service: RepositoryApprovalService,
) -> list[_Row]:
    """Pair every discovered repo with its current approval state."""
    existing = {
        entry.repository_id.lower(): entry
        for entry in service.list_approvals()
    }
    rows: list[_Row] = []
    for repo in candidates:
        approved = existing.get(repo.repository_id.lower())
        rows.append(_Row(
            repo=repo,
            initially_approved=approved is not None,
            initial_remote=approved.remote_url if approved else '',
            initial_mode=approved.approval_mode.value if approved else '',
            selected=approved is not None,
        ))
    return rows


def _render_rows(rows: list[_Row]) -> None:
    """Print ``[x]`` / ``[ ]`` lines so the operator sees state at a glance."""
    if not rows:
        print('  (no repositories in scope)')
        return
    width = max(len(r.repo.repository_id) for r in rows)
    for index, row in enumerate(rows, start=1):
        mark = '[x]' if row.selected else '[ ]'
        suffix = ''
        if row.initially_approved and row.initial_remote != row.repo.remote_url:
            suffix = '  ⚠ remote URL differs from approval — toggle to re-approve'
        print(
            f'  {index:>3}. {mark}  '
            f'{row.repo.repository_id.ljust(width)}  '
            f'[{row.repo.source}]  → {row.repo.remote_url}{suffix}',
        )


def _parse_toggle_input(raw: str, max_index: int) -> list[int]:
    """Parse ``"1,3,5"`` / ``"1 3 5"`` / ``"1-3,7"`` into 0-based indices.

    Returns [] for empty input. Out-of-range entries are dropped
    silently so a typo doesn't blow up the whole input — the
    re-rendered table will show what the operator actually toggled.
    """
    cleaned = raw.replace(',', ' ').strip()
    if not cleaned:
        return []
    out: list[int] = []
    for token in cleaned.split():
        if '-' in token and not token.startswith('-'):
            start_text, _, end_text = token.partition('-')
            try:
                start = int(start_text)
                end = int(end_text)
            except ValueError:
                continue
            for n in range(min(start, end), max(start, end) + 1):
                if 1 <= n <= max_index:
                    out.append(n - 1)
            continue
        try:
            n = int(token)
        except ValueError:
            continue
        if 1 <= n <= max_index:
            out.append(n - 1)
    return out


def _apply_changes(
    rows: list[_Row],
    service: RepositoryApprovalService,
    *,
    trusted: bool,
) -> int:
    """Execute the diff between ``initially_approved`` and ``selected``.

    Returns the number of write operations performed (approves +
    revokes). One Apply pass replaces the old "pick one, run again,
    pick another" loop entirely.
    """
    mode = ApprovalMode.TRUSTED if trusted else ApprovalMode.RESTRICTED
    writes = 0
    for row in rows:
        if not row.changed:
            continue
        if row.selected:
            entry = service.approve(
                row.repo.repository_id, row.repo.remote_url, mode=mode,
            )
            print(
                f'  ✓ approved {entry.repository_id!r} '
                f'(mode={entry.approval_mode.value}, remote={entry.remote_url})',
            )
            writes += 1
        else:
            removed = service.revoke(row.repo.repository_id)
            if removed:
                print(f'  ✗ revoked {row.repo.repository_id!r}')
                writes += 1
    return writes


def _print_no_sources_help(
    *,
    workspaces_root: Path,
    repository_root: Path | None,
    config_path: Path | None,
) -> None:
    """Explain exactly which env values are missing and how to set them.

    Old behaviour was a single "no repositories found" line with no
    diagnosis — operators had to guess whether their ``.env`` was
    being read, whether ``REPOSITORY_ROOT_PATH`` was set, etc. This
    spells it out per source.
    """
    print('no repositories could be discovered. Picker needs at least one source:', file=sys.stderr)
    print('', file=sys.stderr)
    if config_path is None:
        print(
            '  • kato config not found. Looked at ``$KATO_CONFIG``, '
            '``$PWD/.kato/kato.yaml``, ``$PWD/kato.yaml``, '
            '``~/.kato/kato.yaml``. Either run ``kato configure`` or '
            'set ``KATO_CONFIG`` to your config file.',
            file=sys.stderr,
        )
    else:
        print(
            f'  • kato config at {config_path} is loadable but its '
            f'``repositories`` block is empty. Add at least one '
            f'``id: <name>`` / ``remote_url: <url>`` entry.',
            file=sys.stderr,
        )
    if repository_root is None:
        print(
            '  • ``REPOSITORY_ROOT_PATH`` is not set. This is the '
            'folder containing your local git checkouts (the same '
            'one kato pushes task branches into at end of run). '
            'Set it in ``<kato>/.env`` so kato finds it on every '
            'run, e.g. ``REPOSITORY_ROOT_PATH=/path/to/projects``. '
            'A shell variable also works.',
            file=sys.stderr,
        )
    else:
        print(
            f'  • ``REPOSITORY_ROOT_PATH={repository_root}`` is set '
            'but contains no top-level ``<repo>/.git`` directories. '
            'Either point it at the right folder or clone the repo '
            'there.',
            file=sys.stderr,
        )
    if not workspaces_root.is_dir():
        print(
            f'  • ``KATO_WORKSPACES_ROOT={workspaces_root}`` does '
            'not exist yet. This is normal on a fresh machine — '
            'kato creates per-task clones here once it runs.',
            file=sys.stderr,
        )
    print('', file=sys.stderr)
    print(
        'You can also approve directly without the picker: '
        '``kato approve-repo approve <repo_id> --remote <git-url>``.',
        file=sys.stderr,
    )


def _run_interactive() -> int:
    """Unified add/edit/remove picker for REP approvals.

    Layout:

      1. Discover repos from kato config + workspaces +
         REPOSITORY_ROOT_PATH.
      2. Pair each repo with its current approval state.
      3. Print a numbered table with ``[x]`` / ``[ ]`` markers.
      4. Operator types comma-separated indices to TOGGLE selection
         (``1,3,5-7``). Empty input = "I'm done".
      5. Apply: anything newly checked → approve. Anything newly
         unchecked → revoke. Untouched rows = no-op. One write
         pass, regardless of how many adds + removes.

    There is intentionally NO sub-mode for "approve" vs "revoke"
    vs "list" in this flow. The operator sees the current state,
    edits it directly, applies. Same screen, same command, every
    time.
    """
    inventory = _discover_inventory_repositories()
    workspace_root = _resolve_workspaces_root()
    workspace = _discover_workspace_repositories(workspace_root)
    repository_root = _resolve_repository_root()
    checkout = (
        _discover_repository_root_repositories(repository_root)
        if repository_root is not None
        else []
    )
    candidates = _merge_sources(inventory, checkout, workspace)

    print(
        f'inventory: {len(inventory)} repo(s) from kato config; '
        f'workspaces: {len(workspace)} repo(s) under {workspace_root}; '
        f'checkouts: {len(checkout)} repo(s) under '
        f'{repository_root if repository_root else "(REPOSITORY_ROOT_PATH unset)"}',
    )
    if not candidates:
        _print_no_sources_help(
            workspaces_root=workspace_root,
            repository_root=repository_root,
            config_path=_kato_config_path(),
        )
        return 1

    service = RepositoryApprovalService()
    rows = _build_rows(candidates, service)
    while True:
        print()
        print(f'Repositories on the REP approval list (storage: {service.storage_path}):')
        _render_rows(rows)
        print()
        print(
            'Type indices to toggle (``1,3,5-7``), or press Enter to apply '
            'the changes shown. ``q`` to quit without writing.',
        )
        raw = input('> ').strip()
        if raw.lower() in ('q', 'quit', 'exit'):
            print('quit; no changes written.')
            return 0
        if not raw:
            break
        for idx in _parse_toggle_input(raw, len(rows)):
            rows[idx].selected = not rows[idx].selected

    pending = [r for r in rows if r.changed]
    if not pending:
        print('no changes to apply.')
        return 0
    additions = sum(1 for r in pending if r.selected)
    removals = sum(1 for r in pending if not r.selected)
    print(
        f'About to apply {len(pending)} change(s): '
        f'+{additions} approval(s), -{removals} revocation(s).',
    )
    trusted = False
    if additions:
        trusted = prompt_yes_no(
            'Use TRUSTED mode for the approvals? '
            '(no — restricted, kato re-checks the remote URL at runtime; '
            'yes — trusted, runtime URL is not enforced)',
            default=False,
        )
    if not prompt_yes_no('Apply these changes?', default=True):
        print('aborted; no changes written.')
        return 0
    writes = _apply_changes(rows, service, trusted=trusted)
    print(f'done. {writes} change(s) written to {service.storage_path}.')
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
