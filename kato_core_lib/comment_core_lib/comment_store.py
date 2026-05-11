"""JSON-backed per-workspace comment store.

The store lives at ``<workspace>/.kato-comments.json`` so it travels
with the workspace folder — an operator who moves a workspace clone
to another machine carries the comment history with it. Reads /
writes go through ``atomic_write_json`` so a kato crash mid-write
can never leave the file half-serialised.

Thread-safety: a per-store ``threading.RLock`` guards every public
mutation. Concurrent reads on disjoint stores (different workspaces)
don't contend.

Public surface is intentionally narrow. The webserver and the
``CommentService`` (in ``agent_service``) drive the flows that
matter; nothing else should be reaching into the store directly.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from kato_core_lib.comment_core_lib.comment_record import (
    CommentRecord,
    CommentSource,
    CommentStatus,
    KatoCommentStatus,
)
from kato_core_lib.helpers.atomic_json_utils import atomic_write_json
from kato_core_lib.helpers.logging_utils import configure_logger


_STORE_FILENAME = '.kato-comments.json'


class LocalCommentStore(object):
    """JSON file at ``<workspace>/.kato-comments.json`` — CRUD + sync."""

    def __init__(self, workspace_dir: str | Path) -> None:
        self._workspace_dir = Path(workspace_dir)
        self._path = self._workspace_dir / _STORE_FILENAME
        self._lock = threading.RLock()
        self.logger = configure_logger(self.__class__.__name__)

    # ----- public API -----

    @property
    def storage_path(self) -> Path:
        return self._path

    def list(self) -> list[CommentRecord]:
        with self._lock:
            return list(self._load_all())

    def list_for_repo(self, repo_id: str) -> list[CommentRecord]:
        normalised = str(repo_id or '').strip().lower()
        if not normalised:
            return []
        return [
            record for record in self.list()
            if record.repo_id.lower() == normalised
        ]

    def get(self, comment_id: str) -> CommentRecord | None:
        target = str(comment_id or '').strip()
        if not target:
            return None
        with self._lock:
            for record in self._load_all():
                if record.id == target:
                    return record
        return None

    def add(self, record: CommentRecord) -> CommentRecord:
        """Append a new comment (or a reply if ``parent_id`` is set).

        Returns the persisted record so callers can read back any
        defaults the dataclass filled in. Raises ``ValueError`` on
        bad input — empty body / missing repo / stale parent —
        rather than silently dropping data.
        """
        body = str(record.body or '').strip()
        if not body:
            raise ValueError('comment body must be non-empty')
        if not str(record.repo_id or '').strip():
            raise ValueError('comment repo_id must be non-empty')
        if record.parent_id and self.get(record.parent_id) is None:
            raise ValueError(
                f'parent comment {record.parent_id!r} does not exist',
            )
        with self._lock:
            existing = list(self._load_all())
            existing.append(record)
            self._persist(existing)
        return record

    def upsert_remote(self, record: CommentRecord) -> CommentRecord:
        """Insert or update a remote-sourced comment by ``remote_id``.

        Used by the sync path (``pull from source git platform``).
        Matches on ``remote_id`` so re-syncing the same remote
        comment doesn't duplicate it. Local-side fields kato cares
        about (``kato_status``, ``kato_addressed_sha``) are
        preserved across upserts so a fix kato pushed for a
        remote comment isn't blown away on the next sync.
        """
        if record.source != CommentSource.REMOTE.value:
            raise ValueError(
                'upsert_remote is only valid for source=remote records',
            )
        if not record.remote_id:
            raise ValueError('remote_id is required to upsert a remote comment')
        with self._lock:
            existing = list(self._load_all())
            for index, current in enumerate(existing):
                if (
                    current.source == CommentSource.REMOTE.value
                    and current.remote_id == record.remote_id
                ):
                    # Preserve kato pipeline fields on update so a
                    # re-sync after kato has already addressed the
                    # comment doesn't reset its kato_status to IDLE.
                    record.kato_status = current.kato_status
                    record.kato_addressed_sha = current.kato_addressed_sha
                    record.kato_failure_reason = current.kato_failure_reason
                    existing[index] = record
                    self._persist(existing)
                    return record
            existing.append(record)
            self._persist(existing)
        return record

    def update_status(
        self,
        comment_id: str,
        *,
        status: str | None = None,
        resolved_by: str = '',
    ) -> CommentRecord | None:
        """Open / resolve a thread (or its top-of-thread comment).

        Resolving the top-of-thread comment is what marks the
        whole thread resolved on the source git platform on next
        sync; replies inherit the thread's resolved state.
        """
        if status is not None and status not in (
            CommentStatus.OPEN.value,
            CommentStatus.RESOLVED.value,
        ):
            raise ValueError(f'unknown comment status: {status!r}')
        with self._lock:
            existing = list(self._load_all())
            for index, current in enumerate(existing):
                if current.id != comment_id:
                    continue
                if status is not None:
                    current.status = status
                    if status == CommentStatus.RESOLVED.value:
                        current.resolved_by = resolved_by or current.resolved_by
                        current.resolved_at_epoch = time.time()
                    else:
                        current.resolved_by = ''
                        current.resolved_at_epoch = 0.0
                existing[index] = current
                self._persist(existing)
                return current
        return None

    def update_kato_status(
        self,
        comment_id: str,
        *,
        kato_status: str,
        addressed_sha: str = '',
        failure_reason: str = '',
    ) -> CommentRecord | None:
        """Move kato's own pipeline state on a comment.

        Called by the agent_service when an agent run starts /
        finishes. Independent of the operator-facing
        ``CommentStatus`` so kato can be done while the operator
        keeps the thread open for review.
        """
        if kato_status not in {item.value for item in KatoCommentStatus}:
            raise ValueError(f'unknown kato_status: {kato_status!r}')
        with self._lock:
            existing = list(self._load_all())
            for index, current in enumerate(existing):
                if current.id != comment_id:
                    continue
                current.kato_status = kato_status
                if addressed_sha:
                    current.kato_addressed_sha = addressed_sha
                if failure_reason:
                    current.kato_failure_reason = failure_reason
                else:
                    if kato_status == KatoCommentStatus.IDLE.value:
                        current.kato_failure_reason = ''
                existing[index] = current
                self._persist(existing)
                return current
        return None

    def delete(self, comment_id: str) -> bool:
        """Remove a comment (and any direct replies). Returns True on hit."""
        with self._lock:
            existing = list(self._load_all())
            removed = False
            kept: list[CommentRecord] = []
            ids_to_drop = {comment_id}
            # First pass: collect every reply chain rooted at the
            # target so we don't strand orphaned replies.
            changed = True
            while changed:
                changed = False
                for record in existing:
                    if record.id in ids_to_drop:
                        continue
                    if record.parent_id and record.parent_id in ids_to_drop:
                        ids_to_drop.add(record.id)
                        changed = True
            for record in existing:
                if record.id in ids_to_drop:
                    removed = True
                    continue
                kept.append(record)
            if removed:
                self._persist(kept)
            return removed

    def queue_size(self) -> int:
        """Number of comments currently in QUEUED state.

        Drives the "kato has N pending comments" indicator in the
        UI. Cheap — single pass over the JSON.
        """
        return sum(
            1 for record in self.list()
            if record.kato_status == KatoCommentStatus.QUEUED.value
        )

    def next_queued(self) -> CommentRecord | None:
        """Oldest QUEUED comment (FIFO). Empty when the queue is drained.

        The agent_service hook calls this on every "agent went
        idle" tick to drain comments one at a time.
        """
        queued = [
            record for record in self.list()
            if record.kato_status == KatoCommentStatus.QUEUED.value
        ]
        if not queued:
            return None
        queued.sort(key=lambda r: r.created_at_epoch)
        return queued[0]

    # ----- internals -----

    def _load_all(self) -> list[CommentRecord]:
        if not self._path.is_file():
            return []
        try:
            with self._path.open('r', encoding='utf-8') as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning(
                'comment store at %s is unreadable (%s) — treating as empty',
                self._path, exc,
            )
            return []
        if not isinstance(payload, dict):
            return []
        rows = payload.get('comments') or []
        if not isinstance(rows, list):
            return []
        out: list[CommentRecord] = []
        for entry in rows:
            if not isinstance(entry, dict):
                continue
            try:
                out.append(CommentRecord.from_dict(entry))
            except (TypeError, ValueError):
                self.logger.warning(
                    'skipping malformed comment record in %s',
                    self._path,
                )
        return out

    def _persist(self, records: list[CommentRecord]) -> None:
        # Workspace folder is created by WorkspaceManager.create
        # before any agent runs. If the operator manually deleted
        # it, we recreate the parent so the write doesn't fail.
        self._workspace_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            self._path,
            {'comments': [record.to_dict() for record in records]},
            logger=self.logger,
            label=f'comment store at {self._path}',
        )
