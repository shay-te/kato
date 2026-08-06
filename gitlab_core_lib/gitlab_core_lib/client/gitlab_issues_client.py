from __future__ import annotations

from typing import Any, Callable
from urllib.parse import quote

from provider_client_base.provider_client_base.client.issue_client_base import (
    IssueClientBase,
)
from provider_client_base.provider_client_base.data.issue_record import IssueRecord

from gitlab_core_lib.gitlab_core_lib.data.fields import (
    GitLabCommentFields,
    GitLabIssueFields,
)


class GitLabIssuesClient(IssueClientBase):
    provider_name = 'gitlab'

    def __init__(
        self,
        base_url: str,
        token: str,
        project: str,
        max_retries: int = 3,
        *,
        is_operational_comment: Callable[[str], bool] | None = None,
        bot_login: str = '',
        include_comments: bool = True,
        require_bot_mention: bool = False,
    ) -> None:
        super().__init__(base_url, token, timeout=30, max_retries=max_retries)
        self._project = quote(str(project).strip(), safe='')
        self._is_operational_comment: Callable[[str], bool] = (
            is_operational_comment or (lambda _: False)
        )
        # @-mention filter (see IssueClientBase). When ``assignee`` isn't a
        # real username, the bot's actual username is resolved from GET /user.
        self._configure_bot_login(bot_login)
        # Which issue comments reach the agent at all (see
        # IssueClientBase._comment_addressed_elsewhere).
        self._configure_comment_policy(
            include_comments=include_comments,
            require_bot_mention=require_bot_mention,
        )
        self.set_headers({'PRIVATE-TOKEN': token})

    def _fetch_current_user_logins(self) -> tuple:
        """Resolve the bot's GitLab username from ``GET /user`` (best-effort).

        Used only when no real ``assignee`` username was configured, so a
        comment @-mentioning a human is still recognized and skipped.
        """
        try:
            response = self._get_with_retry('/user')
            response.raise_for_status()
            username = str(
                (response.json() or {}).get(GitLabCommentFields.USERNAME, '') or ''
            ).strip().lower()
            return (username,) if username else ()
        except Exception:
            self.logger.exception(
                'failed to resolve bot username via GET /user — the '
                '@-mention filter is disabled for comments until this '
                'succeeds',
            )
            return ()

    def validate_connection(self, project: str, assignee: str, states: list[str]) -> None:
        response = self._get_with_retry(
            f'/projects/{self._project}/issues',
            params={'assignee_username': assignee, 'state': 'all', 'per_page': 1},
        )
        response.raise_for_status()

    def get_assigned_tasks(
        self,
        project: str,
        assignee: str,
        states: list[str],
    ) -> list[IssueRecord]:
        issues = self._paginate_items(
            f'/projects/{self._project}/issues',
            params={
                'assignee_username': assignee,
                'state': 'all',
                'order_by': 'updated_at',
                'sort': 'desc',
                'per_page': 100,
            },
            next_ref=self._next_page_ref,
        )
        allowed_states = self._normalized_allowed_states(states)
        return self._normalize_issue_records(
            issues,
            to_record=self._to_record,
            include=lambda issue: self._matches_allowed_state(
                issue.get(GitLabIssueFields.STATE),
                allowed_states,
            ),
        )

    def add_comment(self, issue_id: str, comment: str) -> None:
        response = self._post_with_retry(
            f'/projects/{self._project}/issues/{issue_id}/notes',
            json={GitLabCommentFields.BODY: comment},
        )
        response.raise_for_status()

    def add_tag(self, issue_id: str, tag_name: str) -> None:
        response = self._put_with_retry(
            f'/projects/{self._project}/issues/{issue_id}',
            json={'add_labels': tag_name},
        )
        response.raise_for_status()

    def remove_tag(self, issue_id: str, tag_name: str) -> None:
        response = self._put_with_retry(
            f'/projects/{self._project}/issues/{issue_id}',
            json={'remove_labels': tag_name},
        )
        response.raise_for_status()

    def move_issue_to_state(self, issue_id: str, field_name: str, state_name: str) -> None:
        normalized_field = str(field_name or '').strip().lower()
        if normalized_field in {'labels', 'label'}:
            response = self._put_with_retry(
                f'/projects/{self._project}/issues/{issue_id}',
                json={'add_labels': state_name},
            )
            response.raise_for_status()
            return
        state_event = (
            'reopen'
            if state_name.strip().lower() in {'open', 'opened', 'reopen'}
            else 'close'
        )
        response = self._put_with_retry(
            f'/projects/{self._project}/issues/{issue_id}',
            json={'state_event': state_event},
        )
        response.raise_for_status()

    # ----- internal record builders -----

    def _to_record(self, payload: dict[str, Any]) -> IssueRecord:
        issue_id = str(payload[GitLabIssueFields.IID])
        comment_entries = self._task_comment_entries(self._issue_comments(issue_id))
        return self._build_record(
            issue_id=issue_id,
            summary=payload.get(GitLabIssueFields.TITLE),
            description=self._build_description_with_comments(
                payload.get(GitLabIssueFields.DESCRIPTION),
                comment_entries,
            ),
            comment_entries=comment_entries,
            tags=self._task_tags(payload.get(GitLabIssueFields.LABELS)),
        )

    def _issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        return self._best_effort_response_items(
            issue_id,
            item_label='comments',
            path=f'/projects/{self._project}/issues/{issue_id}/notes',
            params={'per_page': 100},
            next_ref=self._next_page_ref,
        )

    @staticmethod
    def _next_page_ref(response, path, params, _page_items):
        """GitLab paginates via the ``X-Next-Page`` response header (the next
        page NUMBER, empty at the last page) — mirrors GitLabClient's MR-comment
        loop so issue listing/comments no longer stop at 100."""
        headers = getattr(response, 'headers', None) or {}
        next_page = str(headers.get('X-Next-Page', '') or '').strip()
        if not next_page:
            return None
        try:
            page_num = int(next_page)
        except (TypeError, ValueError):
            return None
        return (path, {**params, 'page': page_num})

    def _task_comment_entries(
        self, comments: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        def extract_author(c: dict) -> object:
            author = self._safe_dict(c, GitLabCommentFields.AUTHOR)
            return author.get(GitLabCommentFields.NAME) or author.get(GitLabCommentFields.USERNAME)

        # Skip system notes AND comments addressed to humans other
        # than the bot user. The former is GitLab-specific machinery
        # noise ("changed status to closed"); the latter is the
        # cross-platform @-mention filter — see
        # provider_client_base.helpers.mention_utils.
        def skip(c: dict) -> bool:
            if c.get(GitLabCommentFields.SYSTEM):
                return True
            return self._comment_addressed_elsewhere(
                c.get(GitLabCommentFields.BODY, ''),
            )

        return self._build_comment_entries(
            comments,
            extract_body=lambda c: str(c.get(GitLabCommentFields.BODY, '') or '').strip(),
            extract_author=extract_author,
            skip=skip,
        )
