#!/usr/bin/env python3
"""Operator-facing CLI for the Restricted Execution Protocol approval list.

Two subcommands, kept minimal so the planning UI / future webserver
endpoint can call the same service without going through this shim:

    approve <repo_id> --remote <git-url> [--trusted]
    revoke  <repo_id>
    list

Approve writes (or upgrades) a row in
``~/.kato/approved-repositories.json``. Revoke removes the row.
List prints what's on record. The shim defers everything to
``RepositoryApprovalService`` so semantics stay co-located with the
preflight gate.
"""

from __future__ import annotations

import argparse
import sys

from kato_core_lib.data_layers.data.repository_approval import ApprovalMode
from kato_core_lib.data_layers.service.repository_approval_service import (
    RepositoryApprovalService,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='kato approve-repo',
        description='Manage the Restricted Execution Protocol approval list.',
    )
    sub = parser.add_subparsers(dest='action', required=True)

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


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
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
