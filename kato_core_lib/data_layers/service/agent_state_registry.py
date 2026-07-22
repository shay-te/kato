from __future__ import annotations

import threading

from agent_core_lib.agent_core_lib.helpers.session_id_utils import fix_session_id
from kato_core_lib.data_layers.data.fields import ImplementationFields, PullRequestFields, StatusFields, TaskFields
from kato_core_lib.helpers.processed_review_comments_store import (
    read_processed_map,
    write_processed_map,
)
from kato_core_lib.helpers.pull_request_context_utils import (
    build_pull_request_context,
    pull_request_context_key,
)
from kato_core_lib.helpers.text_utils import normalized_text


class AgentStateRegistry(object):
    def __init__(self, processed_review_comments_path: object = None) -> None:
        self.pull_request_context_map: dict[str, list[dict[str, str]]] = {}
        self.pull_request_task_map: dict[tuple[str, str], str] = {}
        self.processed_task_map: dict[str, dict[str, object]] = {}
        # processed_review_comment_map is PERSISTED across restarts when a path
        # is supplied (the app wires ~/.kato/processed_review_comments.json at
        # boot). Without it a restart re-works every still-open review comment
        # (they're deliberately left unresolved for the reviewer) — the "same
        # comment answered in a loop" bug. Default None → in-memory only, so
        # tests never read/write real ~/.kato state.
        self._processed_review_comments_path = processed_review_comments_path
        self._processed_review_comments_lock = threading.Lock()
        self.processed_review_comment_map: dict[tuple[str, str], set[str]] = (
            read_processed_map(processed_review_comments_path)
            if processed_review_comments_path
            else {}
        )

    def remember_pull_request_context(
        self,
        pull_request: dict[str, str],
        branch_name: str,
        agent_session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
    ) -> None:
        pull_request_id = pull_request[PullRequestFields.ID]
        context = build_pull_request_context(
            pull_request[PullRequestFields.REPOSITORY_ID],
            branch_name,
            agent_session_id,
            task_id,
            task_summary,
            normalized_text(pull_request.get(PullRequestFields.TITLE, '')),
        )
        existing_contexts = self.pull_request_context_map.setdefault(pull_request_id, [])
        if pull_request_context_key(context) not in {
            pull_request_context_key(existing_context)
            for existing_context in existing_contexts
        }:
            existing_contexts.append(context)
        normalized_task_id = str(task_id or '').strip()
        if normalized_task_id:
            self.pull_request_task_map[
                (
                    str(pull_request[PullRequestFields.REPOSITORY_ID]).strip(),
                    pull_request_id,
                )
            ] = normalized_task_id

    def pull_request_context(
        self,
        pull_request_id: str,
        repository_id: str = '',
    ) -> dict[str, str] | None:
        pull_request_contexts = self.pull_request_context_map.get(pull_request_id, [])
        if repository_id:
            pull_request_contexts = [
                context
                for context in pull_request_contexts
                if context[PullRequestFields.REPOSITORY_ID] == repository_id
            ]
        if not pull_request_contexts:
            return None
        if len(pull_request_contexts) > 1:
            raise ValueError(
                f'ambiguous pull request id across repositories: {pull_request_id}'
            )
        return pull_request_contexts[0]

    def mark_task_processed(self, task_id: str, pull_requests: list[dict[str, str]]) -> None:
        self.processed_task_map[str(task_id)] = {
            StatusFields.STATUS: StatusFields.READY_FOR_REVIEW,
            PullRequestFields.PULL_REQUESTS: [
                dict(pull_request)
                for pull_request in pull_requests
                if isinstance(pull_request, dict)
            ],
        }

    def is_review_comment_processed(
        self,
        repository_id: str,
        pull_request_id: str,
        comment_id: str,
    ) -> bool:
        key = (str(repository_id), str(pull_request_id))
        return str(comment_id) in self.processed_review_comment_map.get(key, set())

    def mark_review_comment_processed(
        self,
        repository_id: str,
        pull_request_id: str,
        comment_id: str,
    ) -> None:
        key = (str(repository_id), str(pull_request_id))
        with self._processed_review_comments_lock:
            self.processed_review_comment_map.setdefault(key, set()).add(str(comment_id))
            self._persist_processed_review_comments_locked()

    def _persist_processed_review_comments_locked(self) -> None:
        """Write the processed-comment map to disk (no-op without a path).

        Caller MUST hold ``_processed_review_comments_lock``. Copies the map so
        the store iterates a stable snapshot, not the live dict.
        """
        if not self._processed_review_comments_path:
            return
        snapshot = {
            key: set(value)
            for key, value in self.processed_review_comment_map.items()
        }
        write_processed_map(self._processed_review_comments_path, snapshot)

    def tracked_task_ids(self) -> set[str]:
        """Return all task IDs that have tracked pull-request contexts."""
        task_ids: set[str] = set()
        for task_id in self.pull_request_task_map.values():
            if task_id:
                task_ids.add(str(task_id))
        for contexts in self.pull_request_context_map.values():
            for context in contexts:
                task_id = str(context.get(TaskFields.ID, '') or '').strip()
                if task_id:
                    task_ids.add(task_id)
        return task_ids

    def session_ids_for_task(self, task_id: str) -> list[str]:
        """Return all session IDs stored in PR contexts for the given task."""
        normalized = str(task_id or '').strip()
        session_ids: list[str] = []
        seen: set[str] = set()
        for contexts in self.pull_request_context_map.values():
            for context in contexts:
                if str(context.get(TaskFields.ID, '') or '').strip() != normalized:
                    continue
                agent_session_id = fix_session_id(context.get(ImplementationFields.AGENT_SESSION_ID))
                if agent_session_id and agent_session_id not in seen:
                    seen.add(agent_session_id)
                    session_ids.append(agent_session_id)
        return session_ids

    def forget_task(self, task_id: str) -> None:
        """Remove all registry entries associated with the given task.

        Also drops the task's PERSISTED processed-review-comment marks, so
        deleting a task leaves nothing behind in
        ~/.kato/processed_review_comments.json — the file never accumulates
        marks for pull requests that no longer belong to any task.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return

        # Collect the task's (repository_id, pull_request_id) keys BEFORE we
        # tear the maps down — from the context map (which carries the repo id
        # per PR) and the task map — so we can drop its processed-comment marks.
        task_pull_request_keys: set[tuple[str, str]] = set()
        for pr_id, contexts in self.pull_request_context_map.items():
            for ctx in contexts:
                if str(ctx.get(TaskFields.ID, '') or '').strip() != normalized:
                    continue
                repository_id = str(
                    ctx.get(PullRequestFields.REPOSITORY_ID, '') or '',
                ).strip()
                if repository_id:
                    task_pull_request_keys.add((repository_id, str(pr_id).strip()))
        for (repo_id, pr_id), tid in self.pull_request_task_map.items():
            if str(tid or '').strip() == normalized:
                task_pull_request_keys.add((str(repo_id).strip(), str(pr_id).strip()))

        # Remove PR context entries that belong exclusively to this task.
        pr_ids_to_remove: list[str] = []
        for pr_id, contexts in self.pull_request_context_map.items():
            remaining = [
                ctx for ctx in contexts
                if str(ctx.get(TaskFields.ID, '') or '').strip() != normalized
            ]
            if not remaining:
                pr_ids_to_remove.append(pr_id)
            else:
                self.pull_request_context_map[pr_id] = remaining
        for pr_id in pr_ids_to_remove:
            del self.pull_request_context_map[pr_id]

        # Remove PR task-map entries for this task.
        stale_keys = [
            key for key, tid in self.pull_request_task_map.items()
            if str(tid or '').strip() == normalized
        ]
        for key in stale_keys:
            del self.pull_request_task_map[key]

        self._forget_processed_review_comments(task_pull_request_keys)

    def _forget_processed_review_comments(
        self,
        pull_request_keys: set[tuple[str, str]],
    ) -> None:
        """Drop (and re-persist) processed marks for the given PR keys.

        So a re-adopted task re-engages its comments, and the on-disk file
        never keeps marks for a deleted task's pull requests.
        """
        if not pull_request_keys:
            return
        with self._processed_review_comments_lock:
            removed = False
            for key in list(self.processed_review_comment_map.keys()):
                normalized_key = (str(key[0]).strip(), str(key[1]).strip())
                if normalized_key in pull_request_keys:
                    del self.processed_review_comment_map[key]
                    removed = True
            if removed:
                self._persist_processed_review_comments_locked()

    def task_id_for_pull_request(
        self,
        pull_request_id: str,
        repository_id: str,
    ) -> str:
        key = (str(repository_id).strip(), str(pull_request_id).strip())
        task_id = self.pull_request_task_map.get(key, '')
        if task_id:
            return task_id
        for processed_task_id, processed_task in self.processed_task_map.items():
            pull_requests = processed_task.get(PullRequestFields.PULL_REQUESTS, [])
            if not isinstance(pull_requests, list):
                continue
            for pull_request in pull_requests:
                if not isinstance(pull_request, dict):
                    continue
                tracked_pull_request_id = str(
                    pull_request.get(PullRequestFields.ID, '') or ''
                ).strip()
                tracked_repository_id = str(
                    pull_request.get(PullRequestFields.REPOSITORY_ID, '') or ''
                ).strip()
                if (
                    tracked_pull_request_id == str(pull_request_id).strip()
                    and tracked_repository_id == str(repository_id).strip()
                ):
                    self.pull_request_task_map[key] = str(processed_task_id)
                    return str(processed_task_id)
        return ''
