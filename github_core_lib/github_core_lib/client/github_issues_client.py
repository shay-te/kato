from __future__ import annotations

from typing import Any, Callable

from provider_client_base.provider_client_base.client.issue_client_base import (
    IssueClientBase,
)
from provider_client_base.provider_client_base.data.issue_record import IssueRecord

from github_core_lib.github_core_lib.data.fields import (
    GitHubCommentFields,
    GitHubIssueFields,
)


class GitHubIssuesClient(IssueClientBase):
    provider_name = 'github'

    def __init__(
        self,
        base_url: str,
        token: str,
        owner: str,
        repo: str,
        max_retries: int = 3,
        *,
        is_operational_comment: Callable[[str], bool] | None = None,
        bot_login: str = '',
        include_comments: bool = True,
        require_bot_mention: bool = False,
    ) -> None:
        super().__init__(base_url, token, timeout=30, max_retries=max_retries)
        self._owner = str(owner).strip()
        self._repo = str(repo).strip()
        self._is_operational_comment: Callable[[str], bool] = (
            is_operational_comment or (lambda _: False)
        )
        # @-mention filter (see IssueClientBase). When ``assignee`` isn't a
        # real login, the bot's actual login is resolved from ``GET /user``.
        self._configure_bot_login(bot_login)
        # Which issue comments reach the agent at all (see
        # IssueClientBase._should_skip_comment).
        self._configure_comment_policy(
            include_comments=include_comments,
            require_bot_mention=require_bot_mention,
        )
        self.set_headers(
            {
                'Authorization': f'Bearer {token}',
                'Accept': 'application/vnd.github+json',
            }
        )

    def _fetch_current_user_logins(self) -> tuple:
        """Resolve the bot's GitHub login from ``GET /user`` (best-effort).

        Used only when no real ``assignee`` login was configured, so a
        comment @-mentioning a human is still recognized and skipped.
        """
        try:
            response = self._get_with_retry('/user')
            response.raise_for_status()
            login = str(
                (response.json() or {}).get(GitHubCommentFields.LOGIN, '') or ''
            ).strip().lower()
            return (login,) if login else ()
        except Exception:
            self.logger.exception(
                'failed to resolve bot login via GET /user — the '
                '@-mention filter is disabled for comments until this '
                'succeeds',
            )
            return ()

    def validate_connection(self, project: str, assignee: str, states: list[str]) -> None:
        response = self._get_with_retry(
            f'/repos/{self._owner}/{self._repo}/issues',
            params={'assignee': assignee, 'state': 'all', 'per_page': 1},
        )
        response.raise_for_status()

    def get_assigned_tasks(
        self,
        project: str,
        assignee: str,
        states: list[str],
    ) -> list[IssueRecord]:
        issues = self._paginate_items(
            f'/repos/{self._owner}/{self._repo}/issues',
            params={
                'assignee': assignee,
                'state': 'all',
                'sort': 'updated',
                'direction': 'desc',
                'per_page': 100,
            },
            next_ref=self._next_page_ref,
        )
        allowed_states = self._normalized_allowed_states(states)
        return self._normalize_issue_records(
            issues,
            to_record=self._to_record,
            include=lambda issue: (
                not issue.get(GitHubIssueFields.PULL_REQUEST)
                and self._matches_allowed_state(
                    issue.get(GitHubIssueFields.STATE),
                    allowed_states,
                )
            ),
        )

    def add_comment(self, issue_id: str, comment: str) -> None:
        response = self._post_with_retry(
            f'/repos/{self._owner}/{self._repo}/issues/{issue_id}/comments',
            json={GitHubCommentFields.BODY: comment},
        )
        response.raise_for_status()

    def add_tag(self, issue_id: str, tag_name: str) -> None:
        response = self._post_with_retry(
            f'/repos/{self._owner}/{self._repo}/issues/{issue_id}/labels',
            json={'labels': [tag_name]},
        )
        response.raise_for_status()

    def remove_tag(self, issue_id: str, tag_name: str) -> None:
        from urllib.parse import quote as _quote
        response = self._delete_with_retry(
            f'/repos/{self._owner}/{self._repo}/issues/{issue_id}'
            f'/labels/{_quote(tag_name, safe="")}',
        )
        if response.status_code in (200, 204, 404):
            return
        response.raise_for_status()

    def move_issue_to_state(self, issue_id: str, field_name: str, state_name: str) -> None:
        normalized_field = str(field_name or '').strip().lower()
        if normalized_field in {'labels', 'label'}:
            response = self._post_with_retry(
                f'/repos/{self._owner}/{self._repo}/issues/{issue_id}/labels',
                json={GitHubIssueFields.LABELS: [state_name]},
            )
            response.raise_for_status()
            return
        response = self._patch_with_retry(
            f'/repos/{self._owner}/{self._repo}/issues/{issue_id}',
            json={normalized_field or GitHubIssueFields.STATE: state_name.lower()},
        )
        response.raise_for_status()

    # ----- internal record builders -----

    def _to_record(self, payload: dict[str, Any]) -> IssueRecord:
        issue_id = str(payload[GitHubIssueFields.NUMBER])
        comment_entries = self._task_comment_entries(self._issue_comments(issue_id))
        return self._build_record(
            issue_id=issue_id,
            summary=payload.get(GitHubIssueFields.TITLE),
            description=self._build_description_with_comments(
                payload.get(GitHubIssueFields.BODY),
                comment_entries,
            ),
            comment_entries=comment_entries,
            tags=self._task_tags(payload.get(GitHubIssueFields.LABELS)),
        )

    def _issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        return self._best_effort_response_items(
            issue_id,
            item_label='comments',
            path=f'/repos/{self._owner}/{self._repo}/issues/{issue_id}/comments',
            params={'per_page': 100},
            next_ref=self._next_page_ref,
        )

    @staticmethod
    def _next_page_ref(_response, path, params, page_items):
        """GitHub REST paginates by ``page`` + ``per_page``. A page that comes
        back SHORT (fewer than ``per_page`` items) is the last one; otherwise
        ask for the next page number. ``None`` at the end. (Page-count based,
        not Link-header parsing — same result, one fewer moving part.)"""
        per_page = int(params.get('per_page', 0) or 0)
        if per_page <= 0 or len(page_items) < per_page:
            return None
        next_page = int(params.get('page', 1) or 1) + 1
        return (path, {**params, 'page': next_page})

    def _task_comment_entries(
        self, comments: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        return self._build_comment_entries(
            comments,
            extract_body=lambda c: str(c.get(GitHubCommentFields.BODY, '') or '').strip(),
            extract_author=lambda c: self._safe_dict(c, GitHubCommentFields.USER).get(
                GitHubCommentFields.LOGIN
            ),
            # Drop comments addressed to humans other than the bot
            # user — see IssueClientBase._should_skip_comment.
            skip=lambda c: self._should_skip_comment(
                c.get(GitHubCommentFields.BODY, ''),
            ),
        )
