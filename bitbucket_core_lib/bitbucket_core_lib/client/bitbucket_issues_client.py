from __future__ import annotations

import re
from typing import Any, Callable

from bitbucket_core_lib.bitbucket_core_lib.client.auth import bitbucket_basic_auth_header
from bitbucket_core_lib.bitbucket_core_lib.data.fields import (
    BitbucketIssueCommentFields,
    BitbucketIssueFields,
)
from provider_client_base.provider_client_base.client.issue_client_base import (
    IssueClientBase,
)
from provider_client_base.provider_client_base.data.issue_record import IssueRecord
from provider_client_base.provider_client_base.helpers.mention_utils import (
    extract_mention_logins,
)
from provider_client_base.provider_client_base.helpers.text_utils import normalized_text

# Bitbucket Cloud raw-markup mention, e.g. ``@{557058:f58131cb-…}`` or
# ``@{uuid}``. The braces are the mention delimiter, so the captured
# group is the bare account_id / uuid.
_BITBUCKET_BRACE_MENTION = re.compile(r'@\{([^}]+)\}')


class BitbucketIssuesClient(IssueClientBase):
    provider_name = 'bitbucket'

    def __init__(
        self,
        base_url: str,
        token: str,
        workspace: str,
        repo_slug: str,
        max_retries: int = 3,
        *,
        username: str = '',
        is_operational_comment: Callable[[str], bool] | None = None,
        bot_login: str = '',
    ) -> None:
        super().__init__(base_url, token, timeout=30, max_retries=max_retries)
        self._workspace = str(workspace).strip()
        self._repo_slug = str(repo_slug).strip()
        self._is_operational_comment: Callable[[str], bool] = (
            is_operational_comment or (lambda _: False)
        )
        # @-mention filter (see IssueClientBase). Bitbucket comments encode
        # mentions as ``@{account_id}``, and the configured ``assignee`` is
        # usually a display_name/nickname — so when no usable login is set
        # the bot's real account_id/uuid/nickname is resolved from /2.0/user.
        self._configure_bot_login(bot_login)
        auth_username = normalized_text(username)
        if auth_username:
            self.set_headers({'Authorization': bitbucket_basic_auth_header(auth_username, token)})

    def _fetch_current_user_logins(self) -> tuple:
        """Resolve the bot's Bitbucket identities from ``GET /2.0/user``.

        Returns the lowercased account_id / nickname / uuid (braces
        stripped) so a mention by any of those forms matches. Best-effort:
        returns ``()`` on any error.
        """
        try:
            response = self._get_with_retry('/2.0/user')
            response.raise_for_status()
            payload = response.json() or {}
            identities = []
            # ``account_id`` is what ``@{…}`` mentions carry; ``uuid`` is an
            # alternative key; ``nickname`` covers plain ``@nickname`` text.
            for field in ('account_id', 'nickname', 'uuid'):
                value = str(payload.get(field, '') or '').strip().strip('{}').lower()
                if value:
                    identities.append(value)
            return tuple(identities)
        except Exception:
            return ()

    def _extract_comment_mentions(self, body: object) -> list:
        """Mention identities from a Bitbucket raw comment body.

        Bitbucket Cloud writes mentions as ``@{account_id}`` (which the
        plain ``@login`` regex can't see because of the brace), so extract
        those explicitly and also fall back to plain ``@nickname`` text.
        """
        text = str(body or '')
        mentions = [
            match.group(1).strip().strip('{}')
            for match in _BITBUCKET_BRACE_MENTION.finditer(text)
        ]
        mentions.extend(extract_mention_logins(text))
        return mentions

    def validate_connection(self, project: str, assignee: str, states: list[str]) -> None:
        response = self._get_with_retry(
            f'/repositories/{self._workspace}/{self._repo_slug}/issues',
            params={'pagelen': 1},
        )
        response.raise_for_status()

    def get_assigned_tasks(
        self,
        project: str,
        assignee: str,
        states: list[str],
    ) -> list[IssueRecord]:
        response = self._get_with_retry(
            f'/repositories/{self._workspace}/{self._repo_slug}/issues',
            params={'pagelen': 100},
        )
        response.raise_for_status()
        allowed_states = self._normalized_allowed_states(states)
        normalized_assignee = str(assignee or '').strip().lower()
        return self._normalize_issue_records(
            self._json_items(response, items_key='values'),
            to_record=self._to_record,
            include=lambda issue: (
                (
                    not normalized_assignee
                    or self._matches_assignee(
                        issue.get(BitbucketIssueFields.ASSIGNEE),
                        normalized_assignee,
                    )
                )
                and self._matches_allowed_state(
                    issue.get(BitbucketIssueFields.STATE),
                    allowed_states,
                )
            ),
        )

    def add_comment(self, issue_id: str, comment: str) -> None:
        response = self._post_with_retry(
            f'/repositories/{self._workspace}/{self._repo_slug}/issues/{issue_id}/comments',
            json={
                BitbucketIssueCommentFields.CONTENT: {
                    BitbucketIssueCommentFields.RAW: comment,
                },
            },
        )
        response.raise_for_status()

    def move_issue_to_state(self, issue_id: str, field_name: str, state_name: str) -> None:
        response = self._put_with_retry(
            f'/repositories/{self._workspace}/{self._repo_slug}/issues/{issue_id}',
            json={str(field_name or BitbucketIssueFields.STATE): state_name},
        )
        response.raise_for_status()

    def add_tag(self, issue_id: str, label_name: str) -> None:
        # Bitbucket Cloud issues use 'component' as the closest tag equivalent.
        # It is single-valued — a second call overwrites the first tag.
        normalized = normalized_text(label_name)
        if not normalized:
            return
        response = self._put_with_retry(
            f'/repositories/{self._workspace}/{self._repo_slug}/issues/{issue_id}',
            json={'component': {'name': normalized}},
        )
        response.raise_for_status()

    def remove_tag(self, issue_id: str, label_name: str) -> None:
        # Only clears the component when it matches label_name — avoids
        # wiping a different component that was set independently.
        try:
            response = self._get_with_retry(
                f'/repositories/{self._workspace}/{self._repo_slug}/issues/{issue_id}',
            )
            response.raise_for_status()
            component = (response.json() or {}).get('component') or {}
            current = normalized_text(
                component.get('name') if isinstance(component, dict) else ''
            )
        except Exception:
            return
        if current.lower() != normalized_text(label_name).lower():
            return
        response = self._put_with_retry(
            f'/repositories/{self._workspace}/{self._repo_slug}/issues/{issue_id}',
            json={'component': None},
        )
        response.raise_for_status()

    # ----- internal record builders -----

    def _to_record(self, payload: dict[str, Any]) -> IssueRecord:
        issue_id = str(payload[BitbucketIssueFields.ID])
        comment_entries = self._task_comment_entries(self._issue_comments(issue_id))
        content = payload.get(BitbucketIssueFields.CONTENT, {})
        if not isinstance(content, dict):
            content = {}
        return self._build_record(
            issue_id=issue_id,
            summary=payload.get(BitbucketIssueFields.TITLE),
            description=self._build_description_with_comments(
                content.get(BitbucketIssueFields.RAW),
                comment_entries,
            ),
            comment_entries=comment_entries,
            tags=self._task_tags(payload.get(BitbucketIssueFields.LABELS)),
        )

    def _issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        return self._best_effort_response_items(
            issue_id,
            item_label='comments',
            path=f'/repositories/{self._workspace}/{self._repo_slug}/issues/{issue_id}/comments',
            params={'pagelen': 100},
            items_key='values',
        )

    def _task_comment_entries(self, comments: list[dict[str, Any]]) -> list[dict[str, str]]:
        def extract_body(c: dict) -> str:
            content = self._safe_dict(c, BitbucketIssueCommentFields.CONTENT)
            return str(content.get(BitbucketIssueCommentFields.RAW, '') or '').strip()

        def extract_author(c: dict) -> object:
            user = self._safe_dict(c, BitbucketIssueCommentFields.USER)
            return (
                user.get(BitbucketIssueCommentFields.DISPLAY_NAME)
                or user.get(BitbucketIssueCommentFields.NICKNAME)
            )

        return self._build_comment_entries(
            comments,
            extract_body=extract_body,
            extract_author=extract_author,
            # Drop comments addressed to humans other than the kato bot —
            # see IssueClientBase._comment_addressed_elsewhere and the
            # Bitbucket ``@{account_id}`` extractor above.
            skip=lambda c: self._comment_addressed_elsewhere(extract_body(c)),
        )

    # ----- provider-specific filtering -----

    @staticmethod
    def _matches_assignee(assignee: Any, expected: str) -> bool:
        if not isinstance(assignee, dict):
            return False
        candidates = {
            str(assignee.get(BitbucketIssueFields.DISPLAY_NAME, '') or '').strip().lower(),
            str(assignee.get(BitbucketIssueFields.NICKNAME, '') or '').strip().lower(),
        }
        return expected in candidates
