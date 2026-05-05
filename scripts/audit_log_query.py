#!/usr/bin/env python3
"""``./kato history`` — show recent kato task activity.

Reads the append-only log at ``~/.kato/audit.log.jsonl`` (or
``KATO_AUDIT_LOG_PATH`` for tests) and prints a numbered list of
the most recent records. No flags, no sub-modes — just the list.
Operators who need fine-grained filtering can ``cat``/``jq`` the
JSONL directly.
"""

from __future__ import annotations

import sys
from typing import Sequence

from kato_core_lib.helpers.audit_log_utils import (
    default_audit_log_path,
    read_audit_records,
)


# Cap on records shown so a long-lived kato install doesn't dump
# hundreds of lines on every ``kato history`` invocation. Older
# records are still on disk; ``cat`` / ``jq`` the JSONL when needed.
_HISTORY_LIMIT = 30


def _format_row(index: int, record: dict) -> str:
    """Render one record as a single tab-separated, indexed line.

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
        f'{index:>3}.',
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


def main(_argv: Sequence[str] | None = None) -> int:
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
    visible = records[-_HISTORY_LIMIT:]
    for index, record in enumerate(visible, start=1):
        print(_format_row(index, record))
    if len(records) > _HISTORY_LIMIT:
        print(
            f'... showing the last {_HISTORY_LIMIT} of {len(records)} '
            f'record(s). The full log is at {default_audit_log_path()}.',
        )
    return 0


if __name__ == '__main__':
    sys.exit(main())
