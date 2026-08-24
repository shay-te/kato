"""Local + review comment handling for a task — queue, dispatch, lifecycle.

Extracted from ``AgentService``, which had grown to 4,700 lines and 56 public
methods across six unrelated subsystems. This cluster was 42% of it: 53
methods that share one concern (a task's comments — the operator's diff
comments and the provider's PR review comments), one store (``LocalCommentStore``
per workspace), and one scheduler (queued → in-progress → addressed).

It touches only four collaborators, which is why it separates cleanly:
workspace manager (to find the store), session manager (to see whether a turn
is in flight), the review-comment service (provider-side threads), and the
repository service (publishing a fix).

``AgentService`` keeps the same public method names and forwards to this
class. That is deliberate and NOT a re-export shim: the webserver resolves
these dynamically — ``getattr(agent_service, 'list_task_comments', None)`` —
so a rename silently degrades a route to "feature missing" instead of failing
loudly. The names stay put until those call sites are repointed.
"""

from __future__ import annotations

import logging

from kato_core_lib.helpers.comment_store_utils import comment_store_for
from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.service_results import failure
from kato_core_lib.helpers.logging_utils import configure_logger
from utils_core_lib.utils_core_lib.text_utils import text_from_mapping
import uuid
from provider_client_base.provider_client_base.data.review_comment import ReviewComment


class TaskCommentService(object):
    """Owns a task's comment queue, its runs, and their lifecycle."""

    def __init__(
        self,
        *,
        workspace_manager,
        session_manager,
        review_comment_service,
        repository_service,
        parallel_task_runner=None,
        planning_session_runner=None,
        lesson_service=None,
        cleanup_service=None,
        run_service=None,
        logger: logging.Logger | None = None,
    ) -> None:
        # A getter, not a captured logger: the host swaps ``self.logger`` at
        # runtime, and a logger captured at construction would keep writing to
        # the one it was born with — the sub-service would look silent to
        # anything watching the host.
        self._logger_getter = provider_for(
            logger if logger is not None else configure_logger('TaskCommentService'),
        )
        # Collaborators arrive either as the object itself or wrapped in
        # ``later(host, 'attr')`` when the host replaces them at runtime (a
        # workspace manager rebuilt by setup mode). See
        # kato_core_lib.helpers.late_binding.
        self._get_workspace_manager = provider_for(workspace_manager)
        self._get_session_manager = provider_for(session_manager)
        self._get_review_comment_service = provider_for(review_comment_service)
        self._get_repository_service = provider_for(repository_service)
        self._get_parallel_task_runner = provider_for(parallel_task_runner)
        self._get_planning_session_runner = provider_for(planning_session_runner)
        # Callables the host still owns. Injected rather than reached for:
        # lesson capture and done-task cleanup are the host's concerns, and a
        # back-reference to the host would put the cycle straight back.
        self._get_lesson_service = provider_for(lesson_service)
        self._get_cleanup_service = provider_for(cleanup_service)
        self._get_run_service = provider_for(run_service)


    @property
    def _workspace_manager(self):
        return self._get_workspace_manager()

    @property
    def _session_manager(self):
        return self._get_session_manager()

    @property
    def _review_comment_service(self):
        return self._get_review_comment_service()

    @property
    def _repository_service(self):
        return self._get_repository_service()

    @property
    def _parallel_task_runner(self):
        return self._get_parallel_task_runner()

    @property
    def _planning_session_runner(self):
        return self._get_planning_session_runner()

    @property
    def _run_service(self):
        """The run engine — queue → dispatch → completion for these comments."""
        return self._get_run_service()

    @property
    def _cleanup_service(self):
        """Done-task cleanup. ``None`` when the host wires none."""
        return self._get_cleanup_service()

    @property
    def _lesson_service(self):
        """Lesson capture. ``None`` when the host runs without lessons."""
        return self._get_lesson_service()

    @property
    def logger(self):
        """The host's CURRENT logger — resolved per call, never captured."""
        return self._logger_getter()

    def get_new_pull_request_comments(self) -> list[ReviewComment]:
        if self._cleanup_service is not None:
            self._cleanup_service.cleanup_done_task_conversations()
        return self._review_comment_service.get_new_pull_request_comments()

    def active_review_comment_task_ids(self) -> list[str]:
        """Task ids currently running a PR review-comment batch."""
        return self._review_comment_service.active_review_comment_task_ids()

    def stop_review_comment_work(self) -> list[str]:
        """Stop every in-flight PR review-comment run; return the task ids.

        The webserver calls this the moment an operator switches
        ``KATO_REVIEW_COMMENTS_ENABLED`` off, so "stop pulling comments"
        also means "stop the one you're working on right now" rather than
        "stop after this one finishes".
        """
        return self._review_comment_service.stop_active_review_comment_work()

    def handle_pull_request_comment(self, payload: dict) -> dict[str, str]:
        return self._review_comment_service.handle_pull_request_comment(payload)

    def process_review_comment(self, comment: ReviewComment) -> dict[str, str]:
        return self._review_comment_service.process_review_comment(comment)

    def process_review_comment_batch(
        self, comments: list[ReviewComment],
    ) -> list[dict[str, str]]:
        return self._review_comment_service.process_review_comment_batch(comments)

    def task_id_for_review_comment(self, comment: ReviewComment) -> str | None:
        return self._review_comment_service.task_id_for_comment(comment)

    def list_task_comments(
        self, task_id: str, repo_id: str = '',
    ) -> list[dict[str, object]]:
        """Return every comment on a task workspace (optionally per-repo).

        Drives the Changes-tab inline-comment widget. Each entry is
        a ``CommentRecord.to_dict()`` so the UI sees the full set
        of fields (id, body, line, source, status, kato_status,
        author, parent_id for threading).
        """
        store = self.comment_store(task_id)
        if store is None:
            return []
        records = (
            store.list_for_repo(repo_id) if repo_id else store.list()
        )
        # Annotate each comment with ``outdated``: its original anchor
        # line disappeared or now contains different text. The UI moves
        # these threads to the file-level comments panel instead of
        # rendering them on the wrong line.
        anchor_cache: dict[tuple, object] = {}
        out: list[dict[str, object]] = []
        for record in records:
            data = record.to_dict()
            data['outdated'] = self._comment_anchor_is_outdated(
                task_id, record, anchor_cache,
            )
            out.append(data)
        return out

    def _comment_anchor_is_outdated(self, task_id: str, record, cache: dict) -> bool:
        """True when a line-anchored comment no longer matches the file.

        Only line-anchored comments (``line >= 1``) can go stale this way;
        file-level (``line < 1``) comments always render in the file panel.
        Conservative: if the file can't be read (missing, binary, path
        unresolved) we report NOT outdated so a lookup glitch never hides
        a real comment.
        """
        line = int(getattr(record, 'line', -1) or -1)
        if line < 1:
            return False
        repo_id = str(getattr(record, 'repo_id', '') or '').strip()
        file_path = str(getattr(record, 'file_path', '') or '').strip()
        if not repo_id or not file_path:
            return False
        lines_key = ('lines', repo_id, file_path)
        if lines_key not in cache:
            cache[lines_key] = self._file_lines(task_id, repo_id, file_path)
        lines = cache[lines_key]
        if lines is None:
            return False
        if line > len(lines):
            return True
        original_hash = str(getattr(record, 'anchor_line_hash', '') or '')
        if not original_hash:
            return False
        current_text = lines[line - 1]
        return original_hash != self._comment_anchor_line_hash(current_text)

    def _comment_anchor_line_hash(self, text: str) -> str:
        """Stable hash for a line snapshot used by local comments."""
        import hashlib

        return hashlib.sha256(str(text).encode('utf-8')).hexdigest()

    def _file_line_text(
        self, task_id: str, repo_id: str, file_path: str, line: int,
    ) -> str | None:
        """Text of a 1-based workspace file line, without the newline."""
        lines = self._file_lines(task_id, repo_id, file_path)
        if lines is None:
            return None
        index = int(line) - 1
        if index < 0 or index >= len(lines):
            return None
        return lines[index]

    def add_task_comment(
        self,
        task_id: str,
        *,
        repo_id: str,
        file_path: str,
        line: int = -1,
        body: str = '',
        parent_id: str = '',
        author: str = '',
    ) -> dict[str, object]:
        """Persist a new local comment + immediately queue / run kato.

        On create the comment lands as ``kato_status=QUEUED``. If
        the task currently has no live agent turn in flight, kato
        kicks off a review-fix run for this comment right away
        (``KatoCommentStatus.IN_PROGRESS``); otherwise the comment
        sits in the queue and the next "agent went idle" tick
        drains it. This mirrors the operator expectation "submit
        comment → kato fixes immediately if free, queues otherwise."
        """
        from kato_core_lib.comment_core_lib import (
            CommentRecord,
            CommentSource,
            KatoCommentStatus,
        )

        store = self.comment_store(task_id)
        if store is None:
            return failure(
                'no workspace for task — adopt it first',
            )
        record = CommentRecord(
            repo_id=str(repo_id or '').strip(),
            file_path=str(file_path or '').strip(),
            line=int(line if line is not None else -1),
            parent_id=str(parent_id or '').strip(),
            author=str(author or 'operator'),
            body=str(body or '').strip(),
            source=CommentSource.LOCAL.value,
        )
        if not record.parent_id and record.line >= 1:
            anchor_text = self._file_line_text(
                str(task_id), record.repo_id, record.file_path, record.line,
            )
            if anchor_text is not None:
                record.anchor_line_hash = self._comment_anchor_line_hash(
                    anchor_text,
                )
        try:
            persisted = store.add(record)
        except ValueError as exc:
            return failure(
                str(exc),
            )
        self.capture_comment_lesson_candidate(str(task_id), persisted)
        # An operator reply RE-ENGAGES kato: it flips the thread's root
        # comment back to QUEUED (pending) and triggers a run, so kato
        # addresses the new reply (e.g. "no, do it differently") instead
        # of leaving the thread ADDRESSED. The re-run's prompt includes
        # the thread replies (see ``_comment_agent_prompt``) so kato sees
        # the latest pushback. Claude's own replies are added via
        # ``_add_comment_agent_reply`` (store.add directly), NOT this
        # path, so this never self-triggers a loop.
        if persisted.parent_id:
            root = persisted
            seen: set[str] = set()
            while root.parent_id and root.id not in seen:
                seen.add(root.id)
                parent = store.get(root.parent_id)
                if parent is None:
                    break
                root = parent
            store.update_kato_status(
                root.id, kato_status=KatoCommentStatus.QUEUED.value,
            )
            triggered = self._trigger_comment_run(str(task_id), root.id)
            return {
                'ok': True,
                'comment': persisted.to_dict(),
                'triggered_immediately': triggered,
                'requeued_root_id': root.id,
            }
        # Kick off the agent if the task is idle, otherwise queue.
        store.update_kato_status(
            persisted.id, kato_status=KatoCommentStatus.QUEUED.value,
        )
        triggered = self._trigger_comment_run(
            str(task_id), persisted.id,
        )
        persisted = store.get(persisted.id) or persisted
        return {
            'ok': True,
            'comment': persisted.to_dict(),
            'triggered_immediately': triggered,
        }

    def capture_comment_lesson_candidate(self, task_id: str, comment) -> None:
        """Best-effort candidate lesson extraction for operator diff comments."""
        body = str(getattr(comment, 'body', '') or '').strip()
        if not body:
            return
        comment_id = str(getattr(comment, 'id', '') or '').strip()
        candidate_id = self._comment_lesson_candidate_id(task_id, comment_id)
        file_path = str(getattr(comment, 'file_path', '') or '').strip()
        line = int(getattr(comment, 'line', -1) or -1)
        context = (
            f'Operator diff comment for task {task_id}.\n'
            f'File: {file_path or "(none)"}\n'
            f'Line: {line}\n'
            f'Comment:\n{body}'
        )
        if self._lesson_service is not None:
            self._lesson_service.capture_candidate(candidate_id, context)

    def _file_lines(
        self, task_id: str, repo_id: str, file_path: str,
    ) -> list[str] | None:
        """Workspace file lines without newlines, or None when unreadable."""
        if self._workspace_manager is None:
            return None
        try:
            repo_path = self._workspace_manager.repository_path(task_id, repo_id)
        except Exception:
            return None
        target = repo_path / file_path
        try:
            if not target.is_file():
                return None
            with target.open('r', encoding='utf-8', errors='replace') as handle:
                return [text.rstrip('\r\n') for text in handle]
        except Exception:
            return None

    def _file_line_count(self, task_id: str, repo_id: str, file_path: str) -> int | None:
        """Line count of a workspace file, or None when it can't be read."""
        lines = self._file_lines(task_id, repo_id, file_path)
        return None if lines is None else len(lines)


    @staticmethod
    def _comment_lesson_candidate_prefix(task_id: str, comment_id: str) -> str:
        return (
            f'comment__{str(task_id or "").strip()}__'
            f'{str(comment_id or "").strip()}__'
        )

    @classmethod
    def _comment_lesson_candidate_id(cls, task_id: str, comment_id: str) -> str:
        return (
            f'{cls._comment_lesson_candidate_prefix(task_id, comment_id)}'
            f'{uuid.uuid4().hex}'
        )











    def resolve_task_comment(
        self,
        task_id: str,
        comment_id: str,
        *,
        resolved_by: str = '',
    ) -> dict[str, object]:
        """Mark a comment thread resolved (operator-driven).

        Independent of ``kato_status`` — kato may have already
        addressed the comment (``ADDRESSED``) and the operator
        decides whether to keep the thread open for review or
        close it.

        For ``source=remote`` comments, ALSO mirrors the resolve
        back to the source git platform: posts a reply explaining
        why kato thought it was addressed (when applicable) and
        resolves the thread there too. Best-effort — a platform
        failure leaves the local store resolved but flags the
        sync gap in the response so the UI can surface it.
        """
        from kato_core_lib.comment_core_lib import (
            CommentSource,
            CommentStatus,
            KatoCommentStatus,
        )

        store = self.comment_store(task_id)
        if store is None:
            return failure(
                'no workspace for task',
            )
        updated = store.update_status(
            comment_id,
            status=CommentStatus.RESOLVED.value,
            resolved_by=resolved_by or 'operator',
        )
        if updated is None:
            return failure(
                f'comment {comment_id!r} not found',
            )
        remote_sync = {'attempted': False}
        if updated.source == CommentSource.REMOTE.value and updated.remote_id:
            # When kato had already addressed it, post the
            # "addressed" reply too so the source thread carries
            # context. Otherwise just resolve.
            include_reply = (
                updated.kato_status == KatoCommentStatus.ADDRESSED.value
            )
            remote_sync = self._sync_resolve_to_remote(
                task_id, updated, include_reply=include_reply,
            )
        return {'ok': True, 'comment': updated.to_dict(), 'remote_sync': remote_sync}

    def mark_comment_addressed(
        self,
        task_id: str,
        comment_id: str,
        *,
        addressed_sha: str = '',
        post_remote_reply: bool = True,
    ) -> dict[str, object]:
        """Move ``kato_status`` to ADDRESSED + (for remote) post a reply.

        Called after a kato run produces a fix for a comment. Two
        side-effects on remote-sourced comments:

          1. ``kato_status`` flips to ADDRESSED on the local
             record so the UI's pipeline pill switches to
             ``✓ kato addressed``.
          2. Posts the "Kato addressed this review comment and
             pushed a follow-up update" reply on the source git
             platform (same wording as the autonomous review-fix
             flow, via ``review_comment_reply_body``) so reviewers
             see the same thread continuity they get from kato's
             other paths.

        Resolve on the source is left to the operator's explicit
        Resolve click — kato is *not* the right authority to
        decide whether the reviewer's ask is fully addressed,
        only to claim "I shipped a fix, please confirm."
        """
        from kato_core_lib.comment_core_lib import (
            CommentSource,
            KatoCommentStatus,
        )

        store = self.comment_store(task_id)
        if store is None:
            return failure(
                'no workspace for task',
            )
        updated = store.update_kato_status(
            comment_id,
            kato_status=KatoCommentStatus.ADDRESSED.value,
            addressed_sha=str(addressed_sha or ''),
        )
        if updated is None:
            return failure(
                f'comment {comment_id!r} not found',
            )
        if self._lesson_service is not None:
            self._lesson_service.promote_candidates(
                self._comment_lesson_candidate_prefix(task_id, updated.id),
            )
        remote_reply = {'attempted': False}
        if (
            post_remote_reply
            and updated.source == CommentSource.REMOTE.value
            and updated.remote_id
        ):
            remote_reply = self._sync_addressed_reply_to_remote(task_id, updated)
        return {'ok': True, 'comment': updated.to_dict(), 'remote_reply': remote_reply}

    def reopen_task_comment(
        self, task_id: str, comment_id: str,
    ) -> dict[str, object]:
        from kato_core_lib.comment_core_lib import (
            CommentStatus,
            KatoCommentStatus,
        )

        store = self.comment_store(task_id)
        if store is None:
            return failure(
                'no workspace for task',
            )
        updated = store.update_status(
            comment_id, status=CommentStatus.OPEN.value,
        )
        if updated is None:
            return failure(
                f'comment {comment_id!r} not found',
            )
        if updated.parent_id:
            return {'ok': True, 'comment': updated.to_dict()}
        store.update_kato_status(
            comment_id, kato_status=KatoCommentStatus.QUEUED.value,
        )
        triggered = self._trigger_comment_run(str(task_id), comment_id)
        updated = store.get(comment_id) or updated
        return {
            'ok': True,
            'comment': updated.to_dict(),
            'triggered_immediately': triggered,
        }

    def retry_task_comment(
        self, task_id: str, comment_id: str,
    ) -> dict[str, object]:
        """Re-run a comment whose agent turn FAILED.

        Resets it to QUEUED and dispatches immediately when the agent is idle
        (mirrors ``reopen_task_comment``'s re-queue). Only a FAILED comment may
        retry — re-queueing an addressed / in-progress one would double-dispatch
        a turn that already ran. Reopens the operator-facing thread so the retry
        result has somewhere to land.
        """
        from kato_core_lib.comment_core_lib import (
            CommentStatus,
            KatoCommentStatus,
        )

        store = self.comment_store(task_id)
        if store is None:
            return failure(
                'no workspace for task',
            )
        current = store.get(comment_id)
        if current is None:
            return failure(
                f'comment {comment_id!r} not found',
            )
        if current.kato_status != KatoCommentStatus.FAILED.value:
            return failure(
                f'comment {comment_id!r} is not failed '
                    f'(kato_status={current.kato_status!r}) — only a failed '
                    'comment-run can be retried',
            )
        store.update_status(comment_id, status=CommentStatus.OPEN.value)
        store.update_kato_status(
            comment_id, kato_status=KatoCommentStatus.QUEUED.value,
        )
        triggered = self._trigger_comment_run(str(task_id), comment_id)
        updated = store.get(comment_id) or current
        return {
            'ok': True,
            'comment': updated.to_dict(),
            'triggered_immediately': triggered,
        }

    def delete_task_comment(
        self, task_id: str, comment_id: str,
    ) -> dict[str, object]:
        store = self.comment_store(task_id)
        if store is None:
            return failure(
                'no workspace for task',
            )
        removed = store.delete(comment_id)
        return {'ok': bool(removed), 'comment_id': comment_id}

    def edit_task_comment(
        self,
        task_id: str,
        comment_id: str,
        *,
        body: str | None = None,
        kato_status: str | None = None,
    ) -> dict[str, object]:
        """Update a queued local comment's body and/or its kato_status.

        The operator-facing edit flow needs to (a) flip a QUEUED comment
        to ``EDITING`` so ``next_queued`` can't dispatch it while the
        textarea is open, then (b) on save / cancel flip it back to
        ``QUEUED`` (optionally with a new body). One endpoint handles
        both — the caller passes whichever fields apply.

        Hard rules:
          * Only LOCAL comments are editable (remote comments live on
            the source git platform; we don't push edits there).
          * Only the QUEUED ↔ EDITING transition is accepted here.
            Anything else (IN_PROGRESS, ADDRESSED, FAILED, WAITING)
            is the agent's domain — refuse to step on it.
        """
        from kato_core_lib.comment_core_lib import (
            CommentSource,
            KatoCommentStatus,
        )

        store = self.comment_store(task_id)
        if store is None:
            return failure(
                'no workspace for task',
            )
        current = next(
            (record for record in store.list() if record.id == comment_id),
            None,
        )
        if current is None:
            return failure(
                'comment not found',
            )
        if current.source != CommentSource.LOCAL.value:
            return failure(
                'only local comments are editable',
            )
        editable_statuses = {
            KatoCommentStatus.QUEUED.value,
            KatoCommentStatus.EDITING.value,
        }
        if current.kato_status not in editable_statuses:
            return failure(
                f'comment is {current.kato_status!r} — only queued / '
                    f'editing comments can be edited',
            )
        if kato_status is not None and kato_status not in editable_statuses:
            return failure(
                f'cannot transition to {kato_status!r} from the edit '
                    f'flow — only queued / editing are allowed',
            )
        if body is not None:
            store.update_body(comment_id, str(body))
        if kato_status is not None:
            store.update_kato_status(comment_id, kato_status=kato_status)
        updated = next(
            (record for record in store.list() if record.id == comment_id),
            None,
        )
        return {
            'ok': True,
            'comment_id': comment_id,
            'comment': updated.to_dict() if updated is not None else None,
        }

    def sync_remote_comments(
        self, task_id: str, repo_id: str,
    ) -> dict[str, object]:
        """Pull review comments from the source git platform + git pull.

        Two-step:
          1. ``git pull`` on the workspace clone so the line
             numbers in remote comments line up with what the
             operator sees in the diff (a remote comment refers
             to a commit-shaped position; if local HEAD is behind
             those positions are stale).
          2. List PR comments via ``RepositoryService`` and
             ``upsert_remote`` each one into the local store.

        Best-effort: errors are reported in the response so the
        UI can show a toast rather than crashing the picker.
        """
        from kato_core_lib.comment_core_lib import (
            CommentRecord,
            CommentSource,
            CommentStatus,
        )

        store = self.comment_store(task_id)
        if store is None:
            return failure(
                'no workspace for task',
            )
        normalized_repo = str(repo_id or '').strip()
        if not normalized_repo:
            return failure(
                'repo_id is required',
            )
        # Look up the workspace clone for this repo so we can git
        # pull. Resolve via the workspace_manager rather than the
        # inventory entry; the inventory ``local_path`` is the
        # operator's REPOSITORY_ROOT_PATH checkout, which we
        # explicitly don't touch from kato.
        if self._workspace_manager is None:
            return failure(
                'workspace manager not wired',
            )
        try:
            clone_path = self._workspace_manager.repository_path(
                str(task_id), normalized_repo,
            )
        except Exception as exc:
            return failure(
                f'no workspace clone: {exc}',
            )
        if not (clone_path / '.git').is_dir():
            return failure(
                f'workspace clone for {normalized_repo!r} missing',
            )
        # Pull. Best-effort — a failed pull leaves whatever was
        # already on disk (dirty tree, conflict, network error)
        # and we still try to list comments below since the
        # operator might just want the latest comments without
        # the git side.
        try:
            inventory_repo = self._repository_service.get_repository(
                normalized_repo,
            )
        except Exception:
            inventory_repo = None
        pull_result: dict[str, object] = {'ok': True}
        try:
            run_git = getattr(self._repository_service, '_run_git', None)
            if callable(run_git):
                run_git(
                    str(clone_path), ['pull', '--ff-only'],
                    f'failed to git pull workspace clone {clone_path}',
                    inventory_repo,
                )
        except Exception as exc:
            pull_result = {'ok': False, 'error': str(exc)}
        # List PR comments. The agent_service already has the
        # state-registry that tracks pull request id per task.
        synced: list[dict[str, object]] = []
        try:
            list_comments = getattr(
                self._repository_service, 'list_pull_request_comments', None,
            )
            if not callable(list_comments) or inventory_repo is None:
                return {
                    'ok': True, 'pull': pull_result,
                    'synced': [], 'note': (
                        'platform listing unavailable; pulled git only'
                    ),
                }
            pr_id = self._task_pull_request_id(str(task_id), normalized_repo)
            if not pr_id:
                return {
                    'ok': True, 'pull': pull_result,
                    'synced': [], 'note': (
                        'no pull request id on file for this repo + task'
                    ),
                }
            for entry in list_comments(inventory_repo, pr_id) or []:
                remote_id = str(
                    entry.get('id') or entry.get('comment_id') or '',
                ).strip()
                body = str(entry.get('content') or entry.get('body') or '').strip()
                if not remote_id or not body:
                    continue
                record = CommentRecord(
                    repo_id=normalized_repo,
                    file_path=str(entry.get('file_path') or ''),
                    line=int(entry.get('line') or -1),
                    parent_id=str(entry.get('parent_id') or ''),
                    author=str(entry.get('author') or ''),
                    body=body,
                    source=CommentSource.REMOTE.value,
                    remote_id=remote_id,
                    status=(
                        CommentStatus.RESOLVED.value
                        if entry.get('resolved')
                        else CommentStatus.OPEN.value
                    ),
                )
                store.upsert_remote(record)
                synced.append({'remote_id': remote_id, 'file_path': record.file_path})
        except Exception as exc:
            self.logger.exception(
                'failed to sync remote comments for task %s repo %s',
                task_id, repo_id,
            )
            return failure(
                str(exc),
                pull=pull_result,
            )
        return {'ok': True, 'pull': pull_result, 'synced': synced}

    def _sync_resolve_to_remote(
        self, task_id: str, comment, *, include_reply: bool,
    ) -> dict[str, object]:
        """Mirror an operator-resolve back to the source git platform.

        Posts an optional "Kato addressed…" reply (when kato had
        actually addressed the comment), then calls
        ``resolve_review_comment``. Best-effort each step.
        """
        result: dict[str, object] = {'attempted': True}
        try:
            inventory_repo = self._repository_service.get_repository(
                comment.repo_id,
            )
        except Exception as exc:
            result['error'] = f'inventory lookup failed: {exc}'
            return result
        pr_id = self._task_pull_request_id(task_id, comment.repo_id)
        if not pr_id:
            result['error'] = (
                'no pull request id on file — kato cannot resolve the '
                'remote thread without one. This is normal when no PR '
                'has been opened yet.'
            )
            return result
        # Build a minimal ReviewComment-like object: the publish
        # service only reads ``comment_id`` and ``pull_request_id``
        # off the argument, so a SimpleNamespace works.
        from types import SimpleNamespace
        comment_obj = SimpleNamespace(
            comment_id=comment.remote_id,
            pull_request_id=pr_id,
            repository_id=comment.repo_id,
        )
        if include_reply and comment.kato_addressed_sha:
            try:
                from kato_core_lib.helpers.review_comment_utils import (
                    review_comment_reply_body,
                )
                body = review_comment_reply_body({
                    'success': True,
                    'message': (
                        f'Addressed in commit '
                        f'{comment.kato_addressed_sha[:8]}.'
                    ),
                })
                self._repository_service.reply_to_review_comment(
                    inventory_repo, comment_obj, body,
                )
                result['reply_posted'] = True
            except Exception as exc:
                result['reply_error'] = str(exc)
        try:
            self._repository_service.resolve_review_comment(
                inventory_repo, comment_obj,
            )
            result['resolved'] = True
        except Exception as exc:
            result['resolve_error'] = str(exc)
        return result

    def _sync_addressed_reply_to_remote(
        self, task_id: str, comment,
    ) -> dict[str, object]:
        """Post the "kato addressed this" reply on the source thread.

        Same wording as the autonomous review-fix flow uses. Does
        NOT resolve the thread — leaving "should I close this?"
        as an explicit operator click.
        """
        result: dict[str, object] = {'attempted': True}
        try:
            inventory_repo = self._repository_service.get_repository(
                comment.repo_id,
            )
        except Exception as exc:
            result['error'] = f'inventory lookup failed: {exc}'
            return result
        pr_id = self._task_pull_request_id(task_id, comment.repo_id)
        if not pr_id:
            result['error'] = (
                'no pull request id on file — reply will be posted '
                'on the next sync once the PR is opened.'
            )
            return result
        from types import SimpleNamespace
        comment_obj = SimpleNamespace(
            comment_id=comment.remote_id,
            pull_request_id=pr_id,
            repository_id=comment.repo_id,
        )
        try:
            from kato_core_lib.helpers.review_comment_utils import (
                review_comment_reply_body,
            )
            body = review_comment_reply_body({
                'success': True,
                'message': (
                    f'Addressed in commit '
                    f'{comment.kato_addressed_sha[:8]}.'
                    if comment.kato_addressed_sha
                    else 'Addressed.'
                ),
            })
            self._repository_service.reply_to_review_comment(
                inventory_repo, comment_obj, body,
            )
            result['reply_posted'] = True
        except Exception as exc:
            result['reply_error'] = str(exc)
        return result

    def _trigger_comment_run(self, task_id: str, comment_id: str) -> bool:
        """Ask the run engine to start this comment now, if the task is idle.

        ``False`` means the comment stays QUEUED for the next drain — including
        when no run engine is wired at all.
        """
        if self._run_service is None:
            return False
        return self._run_service.trigger_comment_run(task_id, comment_id)

    def comment_store(self, task_id: str):
        """This task's local comment store, or ``None`` when it has no workspace.

        Bound to this service's workspace manager; the policy itself lives in
        :func:`kato_core_lib.helpers.comment_store_utils.comment_store_for` so
        the run engine and the sync path resolve the store identically.
        """
        return comment_store_for(self._workspace_manager, task_id)


















    def _task_pull_request_id(self, task_id: str, repo_id: str) -> str:
        """Find the source-platform PR id for a (task, repo).

        Two sources, in order:

          1. ``AgentStateRegistry`` — the review-comment service
             writes PR contexts here as it discovers them on every
             scan tick. Cheap, accurate when populated.
          2. ``RepositoryService.find_pull_requests`` against the
             task branch — falls back to a live API call when the
             registry hasn't seen this task yet (e.g. an operator
             who adopted a task that hadn't gone through the scan
             loop).

        Empty string when neither source produces a hit. Callers
        treat that as "no PR yet" and skip the platform-side push.
        """
        normalized_task_id = str(task_id or '').strip()
        normalized_repo_id = str(repo_id or '').strip()
        if not normalized_task_id or not normalized_repo_id:
            return ''
        # 1. Registry lookup. Best-effort — defensive against
        # a registry shape change.
        registry = getattr(
            self._review_comment_service, 'state_registry', None,
        )
        list_contexts = getattr(registry, 'list_pull_request_contexts', None)
        if callable(list_contexts):
            try:
                contexts = list_contexts() or []
            except Exception:
                contexts = []
            for context in contexts:
                if not isinstance(context, dict):
                    continue
                ctx_task = text_from_mapping(context, 'task_id')
                ctx_repo = text_from_mapping(context, 'repository_id')
                ctx_pr = text_from_mapping(context, 'pull_request_id')
                if ctx_task == normalized_task_id and ctx_repo == normalized_repo_id and ctx_pr:
                    return ctx_pr
        # 2. Live find_pull_requests fallback. Compute the task
        # branch on the inventory repo and ask the platform.
        try:
            inventory_repo = self._repository_service.get_repository(
                normalized_repo_id,
            )
        except Exception:
            return ''
        try:
            from types import SimpleNamespace
            task_lite = SimpleNamespace(
                id=normalized_task_id, summary='',
            )
            branch_name = self._repository_service.build_branch_name(
                task_lite, inventory_repo,
            )
        except Exception:
            return ''
        try:
            prs = self._repository_service.find_pull_requests(
                inventory_repo,
                source_branch=branch_name,
                title_prefix=f'{normalized_task_id} ',
            ) or []
        except Exception:
            return ''
        for entry in prs:
            pr_id = str(
                entry.get('id') or entry.get('pull_request_id') or '',
            ).strip()
            if pr_id:
                return pr_id
        return ''

