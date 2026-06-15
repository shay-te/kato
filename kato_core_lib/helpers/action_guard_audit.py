"""Tamper-evident audit log for every Action Guard decision.

One JSON line per BLOCK / approved-ASK / denied-ASK, hash-chained (see
``hash_chain_log``) so the record can't be quietly edited. Stored at
``~/.kato/action-guard-audit.log`` (override ``KATO_ACTION_GUARD_AUDIT_PATH``).

Privacy: the raw command is NEVER written — a credential-read command names
the path to a secret. We store a ``command_digest`` (sha256, for
correlation) plus a short ``command_preview`` with the user's home dir
collapsed to ``~`` and the value truncated.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from kato_core_lib.helpers import hash_chain_log
from kato_core_lib.helpers.kato_paths_utils import kato_home_path

_AUDIT_PATH_ENV_KEY = 'KATO_ACTION_GUARD_AUDIT_PATH'
_PREVIEW_MAX = 120
_HOME_COLLAPSE = re.compile(r'/(?:Users|home)/[^/\s]+/')


def action_guard_audit_path() -> Path:
    """``~/.kato/action-guard-audit.log`` (or the env override)."""
    return kato_home_path('action-guard-audit.log', env_key=_AUDIT_PATH_ENV_KEY)


def _command_preview(command: str) -> str:
    collapsed = _HOME_COLLAPSE.sub('~/', str(command or ''))
    collapsed = ' '.join(collapsed.split())
    if len(collapsed) > _PREVIEW_MAX:
        return collapsed[:_PREVIEW_MAX] + '…'
    return collapsed


def record_action_guard_decision(
    *,
    task_id: str,
    category: str,
    decision: str,
    command: str = '',
    rule_id: str = '',
    request_id: str = '',
    answered_by: str = '',
    audit_log_path: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Append one Action Guard decision to the hash-chained audit log.

    ``decision`` is the OUTCOME label — ``block`` | ``ask_approved`` |
    ``ask_denied`` | ``allow``. Returns the written entry. Best-effort: on
    any I/O error the failure is swallowed (auditing must never break the
    permission pipeline) and an empty dict is returned.
    """
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    entry = {
        'timestamp': stamp.isoformat(),
        'event': 'action_guard_decision',
        'task_id': str(task_id or ''),
        'category': str(category or ''),
        'decision': str(decision or ''),
        'rule_id': str(rule_id or ''),
        'request_id': str(request_id or ''),
        'answered_by': str(answered_by or ''),
        'command_digest': 'sha256:' + hashlib.sha256(
            str(command or '').encode('utf-8'),
        ).hexdigest(),
        'command_preview': _command_preview(command),
    }
    target = Path(audit_log_path) if audit_log_path else action_guard_audit_path()
    try:
        return hash_chain_log.append_chained(target, entry)
    except Exception:
        return {}


def read_action_guard_audit(
    limit: int | None = None, audit_log_path: Path | None = None,
) -> list[dict]:
    """Recent audit entries (oldest first), best-effort."""
    target = Path(audit_log_path) if audit_log_path else action_guard_audit_path()
    return hash_chain_log.read_entries(target, limit=limit)


def verify_action_guard_audit(audit_log_path: Path | None = None) -> tuple[bool, int]:
    """``(ok, first_bad_index)`` — recompute the chain to detect tampering."""
    target = Path(audit_log_path) if audit_log_path else action_guard_audit_path()
    return hash_chain_log.verify_chain(target)
