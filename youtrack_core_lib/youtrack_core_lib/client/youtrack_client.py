from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from provider_client_base.provider_client_base.data.subtask import IssueComment, IssueStatus
from youtrack_core_lib.youtrack_core_lib.client.youtrack_client_base import YouTrackClientBase
from youtrack_core_lib.youtrack_core_lib.data.fields import (
    YouTrackAttachmentFields,
    YouTrackCommentFields,
    YouTrackCustomFieldFields,
    YouTrackTagFields,
)
from youtrack_core_lib.youtrack_core_lib.data.task import Task
from utils_core_lib.utils_core_lib.text_utils import (
    alphanumeric_lower_text,
    dict_from_mapping,
    list_from_mapping,
    normalized_text,
    text_from_mapping,
)


class YouTrackClient(YouTrackClientBase):
    provider_name = 'youtrack'
    supports_subtasks = True
    ISSUES_PATH = '/api/issues'
    READABLE_ID = 'idReadable'
    # The custom field holding an issue's assignee, and the value a command
    # gives it to clear it.
    ASSIGNEE_FIELD = 'Assignee'
    UNASSIGNED_VALUE = 'Unassigned'
    ISSUE_STATUS_FIELDS = (
        f'{READABLE_ID},created,'
        'customFields(name,value(name,login,fullName)),'
        f'parent(issues({READABLE_ID}))'
    )
    LISTED_COMMENT_FIELDS = (
        f'{YouTrackCommentFields.ID},{YouTrackCommentFields.TEXT},'
        f'{YouTrackCommentFields.CREATED},'
        f'{YouTrackCommentFields.AUTHOR}'
        f'({YouTrackCommentFields.LOGIN},{YouTrackCommentFields.FULL_NAME})'
    )
    EVENT_FIELDS = 'id,presentation,$type'
    FIELD_VALUE_FIELDS = 'id,name,$type'
    COMMENT_FIELDS = ','.join(
        [
            YouTrackCommentFields.ID,
            YouTrackCommentFields.TEXT,
            (
                f'{YouTrackCommentFields.AUTHOR}'
                f'({YouTrackCommentFields.LOGIN},{YouTrackCommentFields.NAME})'
            ),
        ]
    )
    ATTACHMENT_FIELDS = ','.join(
        [
            YouTrackAttachmentFields.ID,
            YouTrackAttachmentFields.NAME,
            YouTrackAttachmentFields.MIME_TYPE,
            YouTrackAttachmentFields.CHARSET,
            YouTrackAttachmentFields.METADATA,
            YouTrackAttachmentFields.URL,
        ]
    )
    CUSTOM_FIELD_FIELDS = ','.join(
        [
            YouTrackCustomFieldFields.ID,
            YouTrackCustomFieldFields.NAME,
            YouTrackCustomFieldFields.TYPE,
        ]
    )
    DETAILED_CUSTOM_FIELD_FIELDS = ','.join(
        [
            YouTrackCustomFieldFields.ID,
            YouTrackCustomFieldFields.NAME,
            YouTrackCustomFieldFields.TYPE,
            f'value({FIELD_VALUE_FIELDS})',
            f'possibleEvents({EVENT_FIELDS})',
        ]
    )
    MAX_TEXT_ATTACHMENT_CHARS = 5000
    STATE_MACHINE_CUSTOM_FIELD_TYPE = 'StateMachineIssueCustomField'
    TAG_FIELDS = YouTrackTagFields.NAME

    def __init__(
        self,
        base_url: str,
        token: str,
        max_retries: int = 3,
        *,
        operational_comment_prefixes: tuple[str, ...] = (),
        bot_login: str = '',
        include_comments: bool = True,
        require_bot_mention: bool = False,
    ) -> None:
        super().__init__(
            base_url,
            token,
            timeout=30,
            max_retries=max_retries,
            operational_comment_prefixes=operational_comment_prefixes,
            bot_login=bot_login,
            include_comments=include_comments,
            require_bot_mention=require_bot_mention,
        )

    def validate_connection(self, project: str, assignee: str, states: list[str]) -> None:
        response = self._get_with_retry(
            self.ISSUES_PATH,
            params={
                'query': self._build_assigned_tasks_query(project, assignee, states),
                'fields': 'idReadable',
                '$top': 1,
            },
        )
        self.raise_for_status_with_detail(response)

    def get_assigned_tasks(self, project: str, assignee: str, states: list[str]) -> list[Task]:
        # Every page is read, so teams with more than one page of in-progress
        # issues don't silently miss tasks beyond the first 100.
        query = self._build_assigned_tasks_query(project, assignee, states)
        return self._normalize_issue_tasks(
            list(self._paged_items(
                self.ISSUES_PATH,
                {'query': query, 'fields': 'idReadable,summary,description'},
            )),
            to_task=self._to_task,
        )

    def get_task(self, issue_id: str) -> Task | None:
        """One issue, fetched directly by id. ``None`` when it does not exist.

        Finding a single task by walking the assigned/review/all queues costs
        the operator's WHOLE backlog: every issue in it is fetched and then
        enriched with its own tags, comments and attachments. Measured against
        a real instance — 78 issues, 4.78s — to answer a question about ONE
        of them. The same task by id takes 0.66s, and that is with identical
        enrichment; the saving is simply not reading 77 issues nobody asked
        about.

        Deliberately does NOT filter by assignee or state. The caller already
        knows which task it wants, and the queue walk existed partly to reach
        tasks that had left the active states — restricting this would put
        that hole straight back.
        """
        normalized = str(issue_id or '').strip()
        if not normalized:
            return None
        response = self._get_with_retry(
            f'/api/issues/{normalized}',
            params={'fields': 'idReadable,summary,description'},
        )
        if response.status_code == 404:
            return None
        self.raise_for_status_with_detail(response)
        payload = response.json()
        if not isinstance(payload, dict) or not payload.get('idReadable'):
            return None
        return self._to_task(payload)

    def add_comment(self, issue_id: str, comment: str) -> None:
        response = self._post_with_retry(
            f'/api/issues/{issue_id}/comments',
            json={'text': comment},
        )
        response.raise_for_status()

    def add_pull_request_comment(self, issue_id: str, pull_request_url: str) -> None:
        self.add_comment(issue_id, f'Pull request created: {pull_request_url}')

    def add_tag(self, issue_id: str, tag_name: str) -> None:
        """Attach ``tag_name`` to a YouTrack issue, creating the tag if needed.

        YouTrack identifies a tag by its database ID — ``POST
        /api/issues/<id>/tags`` takes ``{"id": ...}``, and the ``{"name": ...}``
        this used to send is refused with ``400 Bad Request``. So the tag's id is
        found first (among the tags this token can see), the tag is created when
        there is none, and only then is it attached.

        Idempotent: a tag already on the issue is left alone.
        """
        if self._issue_tag_id(issue_id, tag_name):
            return
        tag_id = self._visible_tag_id(tag_name) or self._create_tag(tag_name)
        response = self._post_with_retry(
            f'/api/issues/{issue_id}/tags',
            params={'fields': 'id,name'},
            json={'id': tag_id},
        )
        self.raise_for_status_with_detail(response)

    # Tags the token can see are listed a page at a time; a workspace with more
    # than this many is not scanned past it (the tag is then created, and
    # YouTrack refuses a duplicate name with a clear error rather than a
    # silent second tag).
    _TAG_PAGE_SIZE = 200
    _TAG_MAX_PAGES = 25

    def _visible_tag_id(self, tag_name: str) -> str:
        """The id of the tag named ``tag_name`` among those this token can see, or ''.

        Matched exactly but ignoring case (so ``Scope:A`` is not duplicated as
        ``scope:a``), and client-side rather than through the ``query`` parameter:
        a precaution, since tag names like ``scope:value`` contain the ``:`` that
        YouTrack's search syntax uses, and a miss here would create a duplicate.
        """
        wanted = normalized_text(tag_name).lower()
        for page_number in range(self._TAG_MAX_PAGES):
            response = self._get_with_retry(
                '/api/tags',
                params={
                    'fields': 'id,name',
                    '$top': self._TAG_PAGE_SIZE,
                    '$skip': page_number * self._TAG_PAGE_SIZE,
                },
            )
            self.raise_for_status_with_detail(response)
            page = self._json_items(response)
            for item in page:
                if isinstance(item, dict) and normalized_text(item.get('name', '')).lower() == wanted:
                    return str(item.get('id', '') or '')
            if len(page) < self._TAG_PAGE_SIZE:
                break
        return ''

    def _create_tag(self, tag_name: str) -> str:
        """Create the tag (owned by the token's user) and return its id."""
        response = self._post_with_retry(
            '/api/tags',
            params={'fields': 'id,name'},
            json={'name': tag_name},
        )
        self.raise_for_status_with_detail(response)
        payload = response.json()
        tag_id = str(payload.get('id', '') or '') if isinstance(payload, dict) else ''
        if not tag_id:
            raise RuntimeError(f'YouTrack created the tag {tag_name!r} but returned no id for it')
        return tag_id

    def remove_tag(self, issue_id: str, tag_name: str) -> None:
        """Detach ``tag_name`` from a YouTrack issue.

        Tags must be removed by their tag id, so we look it up first.
        A missing tag is a no-op.
        """
        tag_id = self._issue_tag_id(issue_id, tag_name)
        if not tag_id:
            return
        response = self._delete_with_retry(
            f'/api/issues/{issue_id}/tags/{tag_id}',
        )
        response.raise_for_status()

    def _issue_tag_id(self, issue_id: str, tag_name: str) -> str:
        """Return the YouTrack tag id for ``tag_name`` on this issue, or ''.

        Raises on API failure rather than returning ''. Previously the
        try/except swallowed ALL exceptions, so callers (notably
        ``remove_tag``) couldn't distinguish "tag not present" (a
        no-op success) from "API failed" (operator should retry).
        That mask let silent removal failures look like normal
        no-ops in the logs.
        """
        response = self._get_with_retry(
            f'/api/issues/{issue_id}/tags',
            params={'fields': 'id,name', '$top': 200},
        )
        response.raise_for_status()
        for item in self._json_items(response):
            if not isinstance(item, dict):
                continue
            if normalized_text(item.get('name', '')) == tag_name:
                return str(item.get('id', '') or '')
        return ''

    def move_issue_to_state(self, issue_id: str, field_name: str, state_name: str) -> None:
        field = self._issue_state_field(issue_id, field_name)
        if self._field_value_name(field) == state_name:
            return
        updated_field = self._updated_issue_state_field(
            issue_id,
            field_name,
            state_name,
            field,
        )
        self._assert_issue_state(issue_id, field_name, state_name, updated_field)

    def _issue_state_field(
        self,
        issue_id: str,
        field_name: str,
    ) -> dict[str, Any]:
        field = self._get_issue_custom_field(
            issue_id,
            field_name,
            fields=self.DETAILED_CUSTOM_FIELD_FIELDS,
        )
        field_id = text_from_mapping(field, YouTrackCustomFieldFields.ID)
        if not field_id:
            raise ValueError(f'missing issue field id for: {field_name}')
        field_type = text_from_mapping(field, YouTrackCustomFieldFields.TYPE)
        if not field_type:
            raise ValueError(f'missing issue field type for: {field_name}')
        return field

    def _updated_issue_state_field(
        self,
        issue_id: str,
        field_name: str,
        state_name: str,
        field: dict[str, Any],
    ) -> dict[str, Any]:
        field_id = text_from_mapping(field, YouTrackCustomFieldFields.ID)
        field_type = text_from_mapping(field, YouTrackCustomFieldFields.TYPE)
        if field_type == self.STATE_MACHINE_CUSTOM_FIELD_TYPE:
            return self._move_issue_state_machine_field(issue_id, field, state_name)
        return self._move_issue_value_field(
            issue_id,
            field_id,
            field_name,
            field_type,
            state_name,
        )

    def _assert_issue_state(
        self,
        issue_id: str,
        field_name: str,
        state_name: str,
        updated_field: dict[str, Any],
    ) -> None:
        updated_state_name = self._field_value_name(updated_field)
        if updated_state_name == state_name:
            return
        verified_field = self._get_issue_custom_field(
            issue_id,
            field_name,
            fields=self.DETAILED_CUSTOM_FIELD_FIELDS,
        )
        verified_state_name = self._field_value_name(verified_field)
        if verified_state_name == state_name:
            return
        current_state = verified_state_name or updated_state_name or '<unknown>'
        raise ValueError(
            f'issue {issue_id} field {field_name} did not move to state '
            f'{state_name}; current state is {current_state}'
        )

    # ----- sub-tasks -----

    def create_subtask(self, parent_id: str, summary: str, description: str) -> str:
        """Create an unassigned sub-task of ``parent_id``; return its readable id.

        YouTrack has no "parent" to set when an issue is created, so the issue
        is created in the parent's project and then linked with the command
        ``subtask of <parent>``. A project may assign every new issue to
        someone by default; that assignee is cleared, and a sub-task that stays
        assigned is an error — others pick these up, and one silently assigned
        to the project lead would never be picked up by anyone.

        Joining the parent's sprints is best effort: a board this token can't
        change must not fail a sub-task that already exists.
        """
        database_id, readable_id = self._create_issue(
            self._issue_project_id(parent_id), summary, description,
        )
        try:
            self._apply_command(readable_id, f'subtask of {parent_id}')
        except Exception as exc:
            # The issue exists but no ``find_subtask`` will ever find it, so
            # the error names it — a retry would otherwise leave a second one.
            raise RuntimeError(
                f'YouTrack created {readable_id} but could not make it a '
                f'sub-task of {parent_id}: {exc}'
            ) from exc
        self._ensure_unassigned(readable_id)
        self._join_parent_sprints(parent_id, database_id, readable_id)
        return readable_id

    def find_subtask(self, parent_id: str, marker: str) -> str:
        """The readable id of ``parent_id``'s sub-task whose description holds
        ``marker``, or ''. A blank marker would match any sub-task, so it
        matches none."""
        if not marker:
            return ''
        for issue in self._paged_items(
            self.ISSUES_PATH,
            {'query': f'subtask of: {parent_id}', 'fields': 'idReadable,description'},
        ):
            if isinstance(issue, dict) and marker in str(issue.get('description') or ''):
                return text_from_mapping(issue, self.READABLE_ID)
        return ''

    def assign_issue(self, issue_id: str, assignee: str) -> None:
        """Assign the issue to the login ``assignee`` (YouTrack's ``me`` alias
        works too); '' unassigns it."""
        value = normalized_text(assignee) or self.UNASSIGNED_VALUE
        self._apply_command(issue_id, f'{self.ASSIGNEE_FIELD} {value}')

    def get_issues_status(
        self,
        issue_ids: Sequence[str],
        state_field: str,
    ) -> dict[str, IssueStatus]:
        """State, assignee, creation time and parent of several issues, in ONE query.

        Keyed by the ids asked for (blank and repeated ids dropped). An id the
        query does not return — deleted, moved to another id, or out of this
        token's reach — reads as ``exists=False``; an issue returned but not
        asked for is ignored.
        """
        wanted = list(dict.fromkeys(
            normalized_text(issue_id) for issue_id in issue_ids if normalized_text(issue_id)
        ))
        if not wanted:
            return {}
        # Matched ignoring case: YouTrack resolves ``proj-1`` as ``PROJ-1``.
        found = {
            text_from_mapping(issue, self.READABLE_ID).upper(): issue
            for issue in self._paged_items(
                self.ISSUES_PATH,
                {'query': f'issue id: {", ".join(wanted)}', 'fields': self.ISSUE_STATUS_FIELDS},
            )
            if isinstance(issue, dict)
        }
        statuses: dict[str, IssueStatus] = {}
        for issue_id in wanted:
            issue = found.get(issue_id.upper())
            statuses[issue_id] = (
                self._issue_status(issue_id, issue, state_field)
                if issue else IssueStatus(issue_id, exists=False)
            )
        return statuses

    def list_comments(self, issue_id: str) -> list[IssueComment]:
        """Every comment on the issue, newest first.

        Unfiltered — unlike ``_get_issue_comments``, whose comments become the
        agent's task description and pass the @-mention and visibility policy.
        The text is kept exactly as stored, so a code block's lines read back
        unchanged.
        """
        comments = [
            comment
            for comment in self._paged_items(
                f'/api/issues/{issue_id}/comments',
                {'fields': self.LISTED_COMMENT_FIELDS},
            )
            if isinstance(comment, dict)
        ]
        comments.sort(key=self._created_ms, reverse=True)
        return [self._issue_comment(comment) for comment in comments]

    def add_preformatted_comment(self, issue_id: str, intro: str, preformatted: str) -> str:
        """One comment: ``intro``, a blank line, then ``preformatted`` in a
        fenced code block, which YouTrack's Markdown shows exactly as written.
        Returns the new comment's id ('' when YouTrack sends none)."""
        response = self._post_with_retry(
            f'/api/issues/{issue_id}/comments',
            params={'fields': YouTrackCommentFields.ID},
            json={'text': self._preformatted_text(intro, preformatted)},
        )
        self.raise_for_status_with_detail(response)
        return text_from_mapping(response.json(), YouTrackCommentFields.ID)

    def update_preformatted_comment(
        self,
        issue_id: str,
        comment_id: str,
        intro: str,
        preformatted: str,
    ) -> None:
        """Rewrite a comment made by ``add_preformatted_comment`` in place, in
        the same form, so a record that changes stays one comment."""
        response = self._post_with_retry(
            f'/api/issues/{issue_id}/comments/{comment_id}',
            json={'text': self._preformatted_text(intro, preformatted)},
        )
        self.raise_for_status_with_detail(response)

    @staticmethod
    def _preformatted_text(intro: str, preformatted: str) -> str:
        return f'{intro}\n\n```\n{preformatted}\n```'

    def current_user(self) -> str:
        """The login this token works as, from ``/api/users/me``.

        Raises when it can't be told — unlike the best-effort
        ``_fetch_current_user_logins`` behind the @-mention filter. An answer
        of ``me`` is the alias echoed back, not a login, so it raises too.
        """
        response = self._get_with_retry('/api/users/me', params={'fields': 'login'})
        self.raise_for_status_with_detail(response)
        login = text_from_mapping(response.json(), 'login')
        if not login or login.lower() in self._BOT_LOGIN_ALIASES:
            raise RuntimeError('YouTrack did not report the login this token works as')
        return login

    def _issue_project_id(self, issue_id: str) -> str:
        response = self._get_with_retry(
            f'/api/issues/{issue_id}',
            params={'fields': 'project(id)'},
        )
        self.raise_for_status_with_detail(response)
        project_id = text_from_mapping(dict_from_mapping(response.json(), 'project'), 'id')
        if not project_id:
            raise ValueError(f'YouTrack returned no project for issue {issue_id}')
        return project_id

    def _create_issue(self, project_id: str, summary: str, description: str) -> tuple[str, str]:
        """Create an issue in ``project_id``; return its (database id, readable id)."""
        response = self._post_with_retry(
            self.ISSUES_PATH,
            params={'fields': 'id,idReadable'},
            json={'project': {'id': project_id}, 'summary': summary, 'description': description},
        )
        self.raise_for_status_with_detail(response)
        payload = response.json()
        database_id = text_from_mapping(payload, 'id')
        readable_id = text_from_mapping(payload, self.READABLE_ID)
        if not database_id or not readable_id:
            raise RuntimeError(
                f'YouTrack created an issue in project {project_id} but returned no id for it'
            )
        return database_id, readable_id

    def _apply_command(self, issue_id: str, command: str) -> None:
        """Run a YouTrack command (``subtask of X``, ``Assignee Y``) on one issue."""
        response = self._post_with_retry(
            '/api/commands',
            json={'query': command, 'issues': [{self.READABLE_ID: issue_id}]},
        )
        self.raise_for_status_with_detail(response)

    def _ensure_unassigned(self, issue_id: str) -> None:
        """Clear an assignee a project default put on a new issue; raise if it stays."""
        if not self._issue_assignee_login(issue_id):
            return
        self.assign_issue(issue_id, '')
        still_assigned = self._issue_assignee_login(issue_id)
        if still_assigned:
            raise RuntimeError(
                f'YouTrack issue {issue_id} is still assigned to {still_assigned} '
                'after unassigning it'
            )

    def _issue_assignee_login(self, issue_id: str) -> str:
        response = self._get_with_retry(
            f'/api/issues/{issue_id}',
            params={'fields': 'customFields(name,value(login))'},
        )
        self.raise_for_status_with_detail(response)
        return text_from_mapping(
            self._custom_field_value(response.json(), self.ASSIGNEE_FIELD), 'login',
        )

    def _join_parent_sprints(self, parent_id: str, database_id: str, issue_id: str) -> None:
        """Add the new issue to each sprint its parent is in; failures are logged."""
        sprints = self._best_effort_issue_response_items(
            parent_id,
            item_label='sprints',
            path=f'/api/issues/{parent_id}/sprints',
            params={'fields': 'id,agile(id)'},
        )
        for sprint in sprints:
            sprint_id = text_from_mapping(sprint, 'id')
            agile_id = text_from_mapping(dict_from_mapping(sprint, 'agile'), 'id')
            if not sprint_id or not agile_id:
                continue
            try:
                response = self._post_with_retry(
                    f'/api/agiles/{agile_id}/sprints/{sprint_id}/issues',
                    json={'id': database_id},
                )
                self.raise_for_status_with_detail(response)
            except Exception:
                self.logger.exception(
                    'failed to add %s to sprint %s of board %s', issue_id, sprint_id, agile_id,
                )

    def _issue_status(self, issue_id: str, issue: dict[str, Any], state_field: str) -> IssueStatus:
        assignee = self._custom_field_value(issue, self.ASSIGNEE_FIELD)
        parents = list_from_mapping(dict_from_mapping(issue, 'parent'), 'issues')
        return IssueStatus(
            issue_id,
            state=text_from_mapping(self._custom_field_value(issue, state_field), 'name'),
            assignee=text_from_mapping(assignee, YouTrackCommentFields.LOGIN),
            assignee_name=text_from_mapping(assignee, YouTrackCommentFields.FULL_NAME),
            created_at=self._created_at(issue),
            parent_id=text_from_mapping(parents[0], self.READABLE_ID) if parents else '',
        )

    @classmethod
    def _issue_comment(cls, comment: dict[str, Any]) -> IssueComment:
        author = dict_from_mapping(comment, YouTrackCommentFields.AUTHOR)
        return IssueComment(
            text_from_mapping(comment, YouTrackCommentFields.ID),
            author=text_from_mapping(author, YouTrackCommentFields.LOGIN),
            author_name=text_from_mapping(author, YouTrackCommentFields.FULL_NAME),
            # Not normalized: stripping would change what was written.
            body=str(comment.get(YouTrackCommentFields.TEXT) or ''),
            created_at=cls._created_at(comment),
        )

    @staticmethod
    def _custom_field_value(issue: object, field_name: str) -> dict[str, Any]:
        """The value of the issue's custom field ``field_name``; ``{}`` when the
        issue has no such field or it is empty. A multi-value field (a project
        may make Assignee one) reads as its first value — without that, an
        assigned multi-user Assignee would read as unassigned."""
        for field in list_from_mapping(issue, 'customFields'):
            if not isinstance(field, dict) or field.get(YouTrackCustomFieldFields.NAME) != field_name:
                continue
            value = field.get('value')
            if isinstance(value, list):
                value = value[0] if value else None
            return value if isinstance(value, dict) else {}
        return {}

    @staticmethod
    def _created_ms(item: dict[str, Any]) -> int:
        """``created`` in epoch milliseconds; 0 when YouTrack sent none."""
        created = item.get(YouTrackCommentFields.CREATED)
        return created if isinstance(created, int) else 0

    @classmethod
    def _created_at(cls, item: dict[str, Any]) -> str:
        """``created`` as ISO-8601 UTC (``2026-10-17T10:00:00+00:00``); '' when absent."""
        created = cls._created_ms(item)
        if not created:
            return ''
        return datetime.fromtimestamp(created / 1000, tz=timezone.utc).isoformat()

    def _to_task(self, payload: dict[str, Any]) -> Task:
        issue_id = payload['idReadable']
        tags = self._task_tags(issue_id)
        comment_entries = self._task_comment_entries(self._get_issue_comments(issue_id))
        attachments = self._get_issue_attachments(issue_id)
        return self._build_task(
            issue_id=issue_id,
            summary=payload.get(Task.summary.key),
            description=self._build_task_description(
                payload.get(Task.description.key),
                comment_entries,
                attachments,
            ),
            comment_entries=comment_entries,
            tags=tags,
        )

    def _task_tags(self, issue_id: str) -> list[str]:
        response = self._get_with_retry(
            f'/api/issues/{issue_id}/tags',
            params={'fields': self.TAG_FIELDS, '$top': 100},
        )
        response.raise_for_status()
        tags: list[str] = []
        for item in self._json_items(response):
            if not isinstance(item, dict):
                continue
            tag_name = normalized_text(item.get(YouTrackTagFields.NAME))
            if tag_name:
                tags.append(tag_name)
        return tags

    @staticmethod
    def _build_assigned_tasks_query(project: str, assignee: str, states: list[str]) -> str:
        if not states:
            raise ValueError('states must not be empty')
        state_filter = ', '.join(f'{{{state}}}' for state in states)
        return f'project: {project} assignee: {assignee} State: {state_filter}'

    def _get_issue_custom_field(
        self,
        issue_id: str,
        field_name: str,
        fields: str | None = None,
    ) -> dict[str, Any]:
        response = self._get_with_retry(
            f'/api/issues/{issue_id}/customFields',
            params={'fields': fields or self.CUSTOM_FIELD_FIELDS},
        )
        response.raise_for_status()
        for field in self._json_items(response):
            if isinstance(field, dict) and field.get(YouTrackCustomFieldFields.NAME) == field_name:
                return field
        raise ValueError(f'unknown issue field: {field_name}')

    def _move_issue_value_field(
        self,
        issue_id: str,
        field_id: str,
        field_name: str,
        field_type: str,
        state_name: str,
    ) -> dict[str, Any]:
        response = self._post_with_retry(
            f'/api/issues/{issue_id}/customFields/{field_id}',
            params={'fields': self.DETAILED_CUSTOM_FIELD_FIELDS},
            json={
                YouTrackCustomFieldFields.ID: field_id,
                YouTrackCustomFieldFields.NAME: field_name,
                YouTrackCustomFieldFields.TYPE: field_type,
                'value': {'name': state_name},
            },
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _move_issue_state_machine_field(
        self,
        issue_id: str,
        field: dict[str, Any],
        state_name: str,
    ) -> dict[str, Any]:
        field_id = text_from_mapping(field, YouTrackCustomFieldFields.ID)
        field_name = text_from_mapping(field, YouTrackCustomFieldFields.NAME) or '<unknown>'
        event = self._matching_state_machine_event(field, state_name)
        if event is None:
            raise ValueError(
                f'no YouTrack transition event matched state {state_name} for field {field_name}'
            )
        response = self._post_with_retry(
            f'/api/issues/{issue_id}/customFields/{field_id}',
            params={'fields': self.DETAILED_CUSTOM_FIELD_FIELDS},
            json={
                YouTrackCustomFieldFields.ID: field_id,
                YouTrackCustomFieldFields.TYPE: self.STATE_MACHINE_CUSTOM_FIELD_TYPE,
                'event': event,
            },
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _matching_state_machine_event(
        cls,
        field: dict[str, Any],
        state_name: str,
    ) -> dict[str, Any] | None:
        desired_token = cls._normalized_state_token(state_name)
        for event in field.get('possibleEvents') or []:
            if not isinstance(event, dict):
                continue
            event_id = text_from_mapping(event, YouTrackCustomFieldFields.ID)
            presentation = text_from_mapping(event, 'presentation')
            if (
                cls._normalized_state_token(presentation) != desired_token
                and cls._normalized_state_token(event_id) != desired_token
            ):
                continue
            payload = {
                key: value
                for key, value in event.items()
                if key in {YouTrackCustomFieldFields.ID, 'presentation', YouTrackCustomFieldFields.TYPE}
            }
            payload.setdefault(YouTrackCustomFieldFields.TYPE, 'Event')
            return payload
        return None

    @staticmethod
    def _field_value_name(field: dict[str, Any]) -> str:
        value = field.get('value')
        if isinstance(value, dict):
            return text_from_mapping(value, YouTrackCustomFieldFields.NAME)
        return ''

    @staticmethod
    def _normalized_state_token(value: str) -> str:
        return alphanumeric_lower_text(value)

    def _get_issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        return self._get_issue_items(
            issue_id,
            suffix='comments',
            fields=self.COMMENT_FIELDS,
            item_label='comments',
        )

    def _get_issue_attachments(self, issue_id: str) -> list[dict[str, Any]]:
        return self._get_issue_items(
            issue_id,
            suffix='attachments',
            fields=self.ATTACHMENT_FIELDS,
            item_label='attachments',
        )

    def _get_issue_items(
        self,
        issue_id: str,
        suffix: str,
        fields: str,
        item_label: str,
    ) -> list[dict[str, Any]]:
        return self._best_effort_issue_response_items(
            issue_id,
            item_label=item_label,
            path=f'/api/issues/{issue_id}/{suffix}',
            params={'fields': fields, '$top': 100},
        )

    def _build_task_description(
        self,
        description,
        comment_entries: list[dict[str, str]],
        attachments: list[dict[str, Any]],
    ) -> str:
        return self._build_task_description_with_attachment_sections(
            description,
            comment_entries,
            text_attachment_lines=self._format_text_attachments(attachments),
            screenshot_lines=self._format_screenshot_attachments(attachments),
        )

    def _task_comment_entries(self, comments: list[dict[str, Any]]) -> list[dict[str, str]]:
        return self._build_comment_entries(
            comments,
            extract_body=lambda c: text_from_mapping(c, YouTrackCommentFields.TEXT),
            extract_author=self._comment_author_name,
            extract_author_id=self._comment_author_login,
            # Drop comments that @-mention humans other than the kato
            # bot — those are operator-to-employee discussions, not
            # requests the agent should act on. Comments with no
            # @-mention are kept (general project context). See
            # IssueClientBase._comment_addressed_elsewhere.
            skip=lambda c: self._comment_addressed_elsewhere(
                text_from_mapping(c, YouTrackCommentFields.TEXT),
            ),
        )

    def _format_text_attachments(self, attachments: list[dict[str, Any]]) -> list[str]:
        return self._format_text_attachment_lines(
            attachments,
            is_text_attachment=self._is_text_attachment,
            read_text_attachment=self._read_text_attachment,
            attachment_name=self._attachment_name,
        )

    @staticmethod
    def _format_screenshot_attachments(attachments: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            mime_type = attachment.get(YouTrackAttachmentFields.MIME_TYPE) or ''
            if not mime_type.startswith('image/'):
                continue
            metadata = attachment.get(YouTrackAttachmentFields.METADATA) or 'no metadata'
            url = attachment.get(YouTrackAttachmentFields.URL) or ''
            lines.append(
                f'- {YouTrackClient._attachment_name(attachment)} ({metadata}) {url}'.strip()
            )
        return lines

    def _image_attachment_sources(self, issue_id: str) -> list[dict[str, str]]:
        """``[{'name', 'url'}]`` for the issue's image attachments.

        YouTrack's attachment ``url`` is HOST-LESS (``/api/files/bug.png``), so
        it is useful only to a caller that resolves it against the configured
        base URL and sends the token — which is exactly what the shared
        downloader does. Handing that string to the agent, as the screenshot
        section used to, named a file nothing could open.
        """
        return [
            {
                'name': self._attachment_name(attachment),
                'url': str(attachment.get(YouTrackAttachmentFields.URL) or ''),
            }
            for attachment in self._get_issue_attachments(issue_id)
            if isinstance(attachment, dict)
            and self._is_image_attachment_mime_type(
                attachment.get(YouTrackAttachmentFields.MIME_TYPE)
            )
        ]

    def _read_text_attachment(self, attachment: dict[str, Any]) -> str | None:
        return self._download_text_attachment(
            attachment.get(YouTrackAttachmentFields.URL),
            attachment_name=self._attachment_name(attachment),
            max_chars=self.MAX_TEXT_ATTACHMENT_CHARS,
            charset=str(attachment.get(YouTrackAttachmentFields.CHARSET) or 'utf-8'),
        )

    @classmethod
    def _is_text_attachment(cls, attachment: dict[str, Any]) -> bool:
        return cls._is_text_attachment_mime_type(
            attachment.get(YouTrackAttachmentFields.MIME_TYPE) or ''
        )

    @staticmethod
    def _comment_author_name(comment: dict[str, Any]) -> str:
        author = comment.get(YouTrackCommentFields.AUTHOR) or {}
        if not isinstance(author, dict):
            author = {}
        return str(
            author.get(YouTrackCommentFields.NAME)
            or author.get(YouTrackCommentFields.LOGIN)
            or 'unknown'
        )

    @staticmethod
    def _comment_author_login(comment: dict[str, Any]) -> str:
        """The author's LOGIN — the handle ``/api/users/me`` reports.

        Kept separate from ``_comment_author_name``, which prefers the
        display name ("Jane Doe") for human-readable output. Only the login
        can be compared against the account this client authenticates as, so
        "did I write this comment?" must read this field.
        """
        author = comment.get(YouTrackCommentFields.AUTHOR) or {}
        if not isinstance(author, dict):
            return ''
        return str(author.get(YouTrackCommentFields.LOGIN) or '')

    @staticmethod
    def _attachment_name(attachment: dict[str, Any]) -> str:
        return str(attachment.get(YouTrackAttachmentFields.NAME, 'unknown'))
