"""Dataclass + enums for diff-tab review comments.

Mirrors the Bitbucket / GitHub review-comment shape so a remote
comment pulled from the source git platform fits the same record
without lossy reshaping. Fields the local-only path doesn't use
(``remote_id``, ``resolved_by``) carry empty strings rather than
``None`` so JSON serialisation stays trivial.
"""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import asdict, dataclass, field


class CommentSource(str, enum.Enum):
    """Where the comment originated.

    ``local`` — typed in kato's UI on the diff tab. Lives only in
    the workspace's ``.kato-comments.json``. Drives the immediate
    "queue this for kato to fix" path.

    ``remote`` — pulled from the source git platform (Bitbucket /
    GitHub PR review-comment API). Mirrored into the workspace
    store so the diff tab shows a single merged thread regardless
    of where each comment was originally posted. Sync is
    operator-triggered (the "pull comments" button on the diff tab).
    """

    LOCAL = 'local'
    REMOTE = 'remote'


class CommentStatus(str, enum.Enum):
    """Operator-facing thread state. Mirrors Bitbucket's open / resolved."""

    OPEN = 'open'
    RESOLVED = 'resolved'


class KatoCommentStatus(str, enum.Enum):
    """Kato's own pipeline state for a local comment.

    Independent of ``CommentStatus`` — a comment can be ``OPEN``
    (operator hasn't marked it resolved) while kato is already
    ``ADDRESSED`` (the agent ran and pushed a fix). The UI renders
    both side by side.

    Lifecycle:
      ``IDLE``        — just created, no agent run scheduled (e.g.
                        a remote comment we synced for display only).
      ``QUEUED``      — kato will process when the live agent goes
                        idle. Set when the operator submits a local
                        comment while another turn is in flight.
      ``IN_PROGRESS`` — agent is actively running against this
                        comment.
      ``ADDRESSED``   — agent finished; commit pushed; thread
                        ready to resolve.
      ``FAILED``      — agent ran but produced no commits or
                        errored. The reply on the source platform
                        (when synced) carries the explanation.
    """

    IDLE = 'idle'
    QUEUED = 'queued'
    IN_PROGRESS = 'in_progress'
    ADDRESSED = 'addressed'
    FAILED = 'failed'


@dataclass
class CommentRecord(object):
    """One comment — local or remote — anchored to a diff line."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    repo_id: str = ''
    file_path: str = ''
    # Diff line number, 1-based, on the NEW side of the diff. ``-1``
    # for "file-level" comments (not anchored to a specific line).
    line: int = -1
    # ``parent_id`` is empty for top-of-thread comments; a comment
    # whose ``parent_id`` is another record's ``id`` is a reply in
    # that thread.
    parent_id: str = ''
    author: str = ''
    body: str = ''
    created_at_epoch: float = field(default_factory=time.time)

    source: str = CommentSource.LOCAL.value
    # On a remote-sourced or kato-pushed comment, the platform's
    # own id so we can dedupe on next sync and post replies under
    # the right thread. Empty for unsynced local comments.
    remote_id: str = ''

    status: str = CommentStatus.OPEN.value
    resolved_by: str = ''
    resolved_at_epoch: float = 0.0

    kato_status: str = KatoCommentStatus.IDLE.value
    # Set when kato pushes a fix for this comment so the UI can
    # link the thread to the commit that addressed it.
    kato_addressed_sha: str = ''
    kato_failure_reason: str = ''

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> 'CommentRecord':
        defaults = cls()
        return cls(
            id=str(payload.get('id', '') or defaults.id),
            repo_id=str(payload.get('repo_id', '') or ''),
            file_path=str(payload.get('file_path', '') or ''),
            line=int(payload.get('line', defaults.line) or defaults.line),
            parent_id=str(payload.get('parent_id', '') or ''),
            author=str(payload.get('author', '') or ''),
            body=str(payload.get('body', '') or ''),
            created_at_epoch=float(
                payload.get('created_at_epoch', defaults.created_at_epoch)
                or defaults.created_at_epoch,
            ),
            source=str(payload.get('source', '') or CommentSource.LOCAL.value),
            remote_id=str(payload.get('remote_id', '') or ''),
            status=str(payload.get('status', '') or CommentStatus.OPEN.value),
            resolved_by=str(payload.get('resolved_by', '') or ''),
            resolved_at_epoch=float(payload.get('resolved_at_epoch', 0.0) or 0.0),
            kato_status=str(
                payload.get('kato_status', '')
                or KatoCommentStatus.IDLE.value,
            ),
            kato_addressed_sha=str(payload.get('kato_addressed_sha', '') or ''),
            kato_failure_reason=str(payload.get('kato_failure_reason', '') or ''),
        )
