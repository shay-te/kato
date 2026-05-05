#!/usr/bin/env python3
"""``./kato history`` — show recent kato task activity.

Reads the append-only log at ``~/.kato/audit.log.jsonl`` (or
``KATO_AUDIT_LOG_PATH`` for tests) and prints one row per record.
Filters narrow the result set; absence of any records yields a
friendly empty-state message.

Kept intentionally small — no formatting frameworks, no curses, no
ANSI styling. The audit log is plain JSONL on disk so operators
can also ``cat``/``jq`` it directly when ``kato history`` doesn't
quite fit the question they're asking.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from kato_core_lib.helpers.audit_log_utils import (
    EVENT_TASK_FAILED,
    OUTCOME_FAILURE,
    default_audit_log_path,
    read_audit_records,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='kato history',
        description='Show recent kato task activity from ~/.kato/audit.log.jsonl.',
    )
    parser.add_argument(
        '--last', type=int, default=None, metavar='N',
        help='Show only the last N records.',
    )
    parser.add_argument(
        '--task', type=str, default='', metavar='ID',
        help='Filter to records whose task_id matches.',
    )
    parser.add_argument(
        '--failed', action='store_true',
        help='Show only failed records.',
    )
    return parser


def _filter_records(records, *, last, task, failed) -> list:
    filtered = list(records)
    if task:
        target = task.strip().lower()
        filtered = [
            record for record in filtered
            if str(record.get('task_id', '') or '').strip().lower() == target
        ]
    if failed:
        filtered = [
            record for record in filtered
            if record.get('event') == EVENT_TASK_FAILED
            or record.get('outcome') == OUTCOME_FAILURE
        ]
    if last is not None and last > 0:
        filtered = filtered[-last:]
    return filtered


def _format_row(record: dict) -> str:
    """Render one record as a tab-separated line.

    Compact + greppable. The full record is in the file if the
    operator wants every field.
    """
    timestamp = str(record.get('timestamp', '') or '')
    event = str(record.get('event', '') or '?')
    outcome = str(record.get('outcome', '') or '?')
    task_id = str(record.get('task_id', '') or '')
    summary = _truncate(str(record.get('ticket_summary', '') or ''), 60)
    repos = ','.join(record.get('repositories') or []) or '-'
    branch = str(record.get('branch', '') or '-')
    pr_url = str(record.get('pr_url', '') or '-')
    error = _truncate(str(record.get('error', '') or ''), 80)
    parts = [
        timestamp,
        f'{event}({outcome})',
        task_id or '-',
        summary or '-',
        repos,
        branch,
        pr_url,
    ]
    line = '\t'.join(parts)
    if error:
        line = f'{line}\n        error: {error}'
    return line


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + '…'


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    records = read_audit_records()
    if not records:
        path = default_audit_log_path()
        print(
            f'No kato history yet at {path}.\n'
            'Records are written when a task completes, a review-fix '
            'finishes, or a task fails.',
            file=sys.stderr,
        )
        return 0
    filtered = _filter_records(
        records,
        last=args.last,
        task=args.task,
        failed=args.failed,
    )
    if not filtered:
        print('No records match the filter.', file=sys.stderr)
        return 0
    for record in filtered:
        print(_format_row(record))
    return 0


if __name__ == '__main__':
    sys.exit(main())
