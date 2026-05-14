#!/usr/bin/env python3
"""Operator-facing CLI for the Restricted Execution Protocol approval list.

One picker. No sub-modes, no flags, no scripted variants — every
add/edit/remove/mode-change operation lives behind a single prompt.

Shows every repo kato can find, with a mode marker next to each:

* ``[ ]``  not approved
* ``[r]``  approved in **restricted** mode (runtime URL check stays on)
* ``[t]``  approved in **trusted** mode (URL check skipped)

Operator commands (combine freely, comma- or space-separated):

* ``1,3,5-7``  — toggle selection (add or revoke).
* ``t26``      — mark row 26 as trusted (selects + sets mode).
* ``r26``      — mark row 26 as restricted (selects + sets mode).
* Enter        — apply pending changes.
* ``q``        — quit without writing.

``t``/``r`` work on already-approved rows too, so an operator can
promote restricted → trusted in a single pass without the old
revoke-then-re-add dance.

Sources scanned for repo discovery:

1. The kato config's ``repositories`` block (canonical: id +
   remote_url straight from config). Works for fresh tasks where
   no clone exists yet.
2. ``KATO_WORKSPACES_ROOT/<task>/<repo>/.git`` — kato's per-task
   clones. Useful after kato has actually run something.
3. ``REPOSITORY_ROOT_PATH/<repo>/.git`` — the operator's local
   checkout root (the same folder kato pushes branches to at task
   end). The fresh-task source: kato refuses on REP before any
   per-task clone exists, but the repo lives in this folder.

When NO source can be located (no kato config, no workspaces, no
``REPOSITORY_ROOT_PATH``), we exit with a precise message naming
which env vars are missing and how to set them.
"""

from __future__ import annotations

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
from kato_core_lib.helpers.dotenv_utils import load_dotenv_into_environ

# Belt-and-suspenders: when this script is invoked through the
# ``tools/kato/kato.py`` dispatcher (the normal path) the dispatcher
# has already loaded ``.env``. Developers who run
# ``python scripts/approve_repository.py`` directly skip the
# dispatcher entirely, so we re-run the same loader here. Real env
# vars still win — see ``dotenv_utils`` for the parser semantics.
load_dotenv_into_environ(Path(__file__).resolve().parents[1] / '.env')


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

    ``initially_approved`` / ``initial_mode`` reflect what's on the
    approval list when the picker boots. ``selected`` plus
    ``pending_mode_override`` reflect the operator's edits. The Apply
    pass diff'd the two to compute add / revoke / mode-change ops.

    ``pending_mode_override`` is the per-row mode the operator
    requested with the ``t``/``r`` commands. ``''`` means "use
    whatever the apply-time default ends up being" (the original
    behavior: defaults to restricted for new approvals, leaves
    existing modes untouched for re-approvals).
    """

    repo: _DiscoveredRepository
    initially_approved: bool
    initial_remote: str
    initial_mode: str
    selected: bool
    pending_mode_override: str = ''

    @property
    def effective_mode(self) -> str:
        """Mode this row would land in after Apply.

        Per-row override wins; otherwise the row keeps whatever mode
        it was already approved at; otherwise blank (the apply step
        will fall back to whatever the global default is at that
        point).
        """
        if self.pending_mode_override:
            return self.pending_mode_override
        if self.initially_approved:
            return self.initial_mode
        return ''

    @property
    def changed(self) -> bool:
        """True when this row would produce a write on Apply."""
        if self.selected and not self.initially_approved:
            return True
        if not self.selected and self.initially_approved:
            return True
        if self.selected and self.initially_approved:
            # Same id stays approved — counts as a change if either
            # the remote URL drifted OR the operator asked for a
            # different mode than what's on disk.
            if self.initial_remote != self.repo.remote_url:
                return True
            if (
                self.pending_mode_override
                and self.pending_mode_override != self.initial_mode
            ):
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


def _row_mark(row: _Row) -> str:
    """Visible state for a row.

    * ``[ ]``  — not approved (will not be approved on Apply).
    * ``[r]``  — approved in restricted mode.
    * ``[t]``  — approved in trusted mode.
    * ``[x]``  — approved, mode unknown (legacy / blank initial_mode).

    For a row with a pending mode change (``pending_mode_override``)
    the mark shows the TARGET mode so the operator can read the
    table as "what will be true after Apply".
    """
    if not row.selected:
        return '[ ]'
    mode = row.effective_mode
    if mode == 'trusted':
        return '[t]'
    if mode == 'restricted':
        return '[r]'
    return '[x]'


def _render_rows(rows: list[_Row]) -> None:
    """Print mode-aware state lines so the operator sees state at a glance."""
    if not rows:
        print('  (no repositories in scope)')
        return
    width = max(len(r.repo.repository_id) for r in rows)
    for index, row in enumerate(rows, start=1):
        mark = _row_mark(row)
        suffixes: list[str] = []
        if row.initially_approved and row.initial_remote != row.repo.remote_url:
            suffixes.append('⚠ remote URL differs from approval — toggle to re-approve')
        if (
            row.selected
            and row.initially_approved
            and row.pending_mode_override
            and row.pending_mode_override != row.initial_mode
        ):
            suffixes.append(
                f'mode change pending: {row.initial_mode or "?"} → '
                f'{row.pending_mode_override}',
            )
        suffix = ('  ' + '; '.join(suffixes)) if suffixes else ''
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

    Kept as a thin wrapper around :func:`_parse_picker_input` so
    existing tests + the same range/list/dedupe semantics carry
    over to the new mode-aware parser.
    """
    commands = _parse_picker_input(raw, max_index)
    out: list[int] = []
    for cmd in commands:
        if cmd.action == 'toggle':
            out.extend(cmd.indices)
    return out


@dataclass
class _PickerCommand(object):
    """One parsed unit of operator input.

    ``action`` is one of:
      * ``'toggle'``   — flip selection (add or revoke).
      * ``'trusted'``  — mark these rows for trusted-mode approval.
      * ``'restricted'`` — mark these rows for restricted-mode approval.

    ``indices`` are always 0-based, in input order. The picker loop
    applies commands in order so the operator can chain "toggle this
    on; then set it to trusted" inside one input line.
    """

    action: str
    indices: list[int]


_ACTION_BY_PREFIX = {'t': 'trusted', 'r': 'restricted'}


def _classify_token(token: str) -> tuple[str, str]:
    """Return ``(action, body)`` for one operator-supplied token.

    * ``"26"``    → ``("toggle", "26")``
    * ``"t26"``   → ``("trusted", "26")``
    * ``"r1-5"``  → ``("restricted", "1-5")``

    Tokens that don't match any of those shapes come back as
    ``("toggle", token)`` so the index-expander gets a chance to
    reject them (its own typo handling drops bad bodies silently).
    """
    if not token:
        return 'toggle', token
    head = token[0]
    if head in _ACTION_BY_PREFIX and token[1:2] and (
        token[1:2].isdigit() or token[1:2] == '-'
    ):
        return _ACTION_BY_PREFIX[head], token[1:]
    return 'toggle', token


def _parse_picker_input(raw: str, max_index: int) -> list[_PickerCommand]:
    """Parse the picker's free-form input line into a list of commands.

    Accepts (in any combination, comma- or space-separated):
      * Bare indices / ranges → toggle (add-or-revoke).
        ``1``, ``3-5``, ``7,9``.
      * ``t``-prefixed indices / ranges → set mode to trusted.
        ``t26``, ``t1-5``.
      * ``r``-prefixed indices / ranges → set mode to restricted.
        ``r26``, ``r1-5``.

    Each ``t``/``r`` command also implies a selection (the operator
    wants this row approved; setting mode on a ``[ ]`` row doesn't
    make sense otherwise) — the loop sets ``selected=True`` for
    those rows in addition to ``pending_mode_override``.

    Tokens whose body fails to parse are silently dropped (matches
    the original ``_parse_toggle_input`` "typo doesn't nuke the
    whole input" contract).
    """
    cleaned = raw.replace(',', ' ').strip()
    if not cleaned:
        return []
    commands: list[_PickerCommand] = []
    for token in cleaned.split():
        action, body = _classify_token(token)
        indices = _expand_index_token(body, max_index)
        if indices:
            commands.append(_PickerCommand(action=action, indices=indices))
    return commands


def _expand_index_token(body: str, max_index: int) -> list[int]:
    """Turn one token (``"3"`` or ``"1-5"``) into a list of 0-based indices.

    Out-of-range entries are dropped silently. Returns [] for any
    token that doesn't parse — caller decides what to do.
    """
    if not body:
        return []
    if '-' in body and not body.startswith('-'):
        start_text, _, end_text = body.partition('-')
        try:
            start = int(start_text)
            end = int(end_text)
        except ValueError:
            return []
        out: list[int] = []
        for n in range(min(start, end), max(start, end) + 1):
            if 1 <= n <= max_index:
                out.append(n - 1)
        return out
    try:
        n = int(body)
    except ValueError:
        return []
    if 1 <= n <= max_index:
        return [n - 1]
    return []


def _resolve_row_mode(row: _Row, default_mode: ApprovalMode) -> ApprovalMode:
    """Pick the mode to apply to one row.

    Priority: per-row override (set by ``t``/``r`` commands) → mode
    already on disk for re-approvals → the apply-time default.
    Centralising this here keeps :func:`_apply_changes` flat and
    keeps the priority order testable in isolation.
    """
    if row.pending_mode_override:
        return ApprovalMode.from_string(row.pending_mode_override)
    if row.initially_approved and row.initial_mode:
        return ApprovalMode.from_string(row.initial_mode)
    return default_mode


def _apply_changes(
    rows: list[_Row],
    service: RepositoryApprovalService,
    *,
    trusted: bool,
) -> int:
    """Execute the diff between initial state and operator edits.

    Returns the number of write operations performed (approves +
    revokes). One Apply pass replaces the old "pick one, run again,
    pick another" loop entirely. Per-row ``pending_mode_override``
    lets the operator promote an already-approved repo from
    restricted → trusted (or back) in the same pass.
    """
    default_mode = ApprovalMode.TRUSTED if trusted else ApprovalMode.RESTRICTED
    writes = 0
    for row in rows:
        if not row.changed:
            continue
        if row.selected:
            mode = _resolve_row_mode(row, default_mode)
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
        'Fix at least one of the above and re-run ``kato approve-repo``.',
        file=sys.stderr,
    )


def _run_interactive() -> int:
    """Unified add/edit/remove picker for REP approvals.

    Layout:

      1. Discover repos from kato config + workspaces +
         REPOSITORY_ROOT_PATH.
      2. Pair each repo with its current approval state and mode.
      3. Print a numbered table with mode-aware markers:
         ``[ ]`` not approved, ``[r]`` restricted, ``[t]`` trusted.
      4. Operator types commands until they press Enter:
         * ``1,3,5-7``  — toggle (add or revoke).
         * ``t26``      — mark trusted (selects + sets mode).
         * ``r26``      — mark restricted (selects + sets mode).
         * Enter        — apply.
         * ``q``        — quit without writing.
      5. Apply: each pending row gets one ``service.approve`` /
         ``service.revoke`` call. Per-row ``pending_mode_override``
         lets restricted → trusted promotion happen in one pass.

    There is intentionally NO sub-mode for "approve" vs "revoke"
    vs "list" vs "change-mode" in this flow. The operator sees the
    current state, edits it directly, applies. Same screen, same
    command, every time.
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
            'Commands: ``1,3,5-7`` toggle | ``t26`` mark trusted | '
            '``r26`` mark restricted | Enter apply | ``q`` quit.',
        )
        raw = input('> ').strip()
        if raw.lower() in ('q', 'quit', 'exit'):
            print('quit; no changes written.')
            return 0
        if not raw:
            break
        _apply_picker_commands(rows, _parse_picker_input(raw, len(rows)))

    pending = [r for r in rows if r.changed]
    if not pending:
        print('no changes to apply.')
        return 0
    new_additions = [r for r in pending if r.selected and not r.initially_approved]
    revocations = [r for r in pending if not r.selected]
    mode_changes = [
        r for r in pending
        if r.selected and r.initially_approved
    ]
    print(
        f'About to apply {len(pending)} change(s): '
        f'+{len(new_additions)} new approval(s), '
        f'{len(mode_changes)} mode change(s) / re-approval(s), '
        f'-{len(revocations)} revocation(s).',
    )
    # Trusted-prompt only applies to NEW additions that haven't been
    # given a per-row mode by a ``t``/``r`` command. Re-approvals
    # already carry their target mode (either the disk mode or the
    # operator's override) so the global default doesn't touch them.
    needs_default_mode = [r for r in new_additions if not r.pending_mode_override]
    trusted = False
    if needs_default_mode:
        trusted = prompt_yes_no(
            f'Use TRUSTED mode for the {len(needs_default_mode)} new approval(s) '
            f'that have no explicit mode? '
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


def _apply_picker_commands(
    rows: list[_Row],
    commands: list[_PickerCommand],
) -> None:
    """Mutate ``rows`` according to one batch of parsed commands.

    * ``toggle``     — flip ``selected``. If the row was selected and
      had a pending mode override, the override is dropped (going
      [x|t] → [ ] should reset the row, not leave a phantom mode
      hanging on a deselected entry).
    * ``trusted``    — set ``pending_mode_override='trusted'`` and
      ensure ``selected=True``.
    * ``restricted`` — set ``pending_mode_override='restricted'`` and
      ensure ``selected=True``.

    Kept out of the interactive loop body so the wiring is testable
    without driving stdin.
    """
    for cmd in commands:
        for idx in cmd.indices:
            row = rows[idx]
            if cmd.action == 'toggle':
                row.selected = not row.selected
                if not row.selected:
                    row.pending_mode_override = ''
            elif cmd.action in ('trusted', 'restricted'):
                row.selected = True
                row.pending_mode_override = cmd.action


def main() -> int:
    return _run_interactive()


if __name__ == '__main__':
    sys.exit(main())
