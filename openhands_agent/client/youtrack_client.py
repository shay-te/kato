from typing import Any

from core_lib.client.client_base import ClientBase

from openhands_agent.client.retry import is_retryable_exception, is_retryable_response
from openhands_agent.data_layers.data.task import Task


class YouTrackClient(ClientBase):
    COMMENT_FIELDS = 'id,text,author(login,name)'
    ATTACHMENT_FIELDS = 'id,name,mimeType,charset,metaData,url'
    MAX_TEXT_ATTACHMENT_CHARS = 5000
    def __init__(self, base_url: str, token: str, max_retries: int = 3) -> None:
        super().__init__(base_url.rstrip('/'))
        self.set_headers({'Authorization': f'Bearer {token}'})
        self.set_timeout(30)
        self.max_retries = max(1, max_retries)

    def get_assigned_tasks(self, project: str, assignee: str, states: list[str]) -> list[Task]:
        query = self._build_assigned_tasks_query(project, assignee, states)
        response = self._get_with_retry(
            '/api/issues',
            params={'query': query, 'fields': 'idReadable,summary,description'},
        )
        response.raise_for_status()
        tasks: list[Task] = []
        for item in response.json() or []:
            try:
                tasks.append(self._to_task(item))
            except (KeyError, TypeError, ValueError):
                continue
        return tasks

    def add_pull_request_comment(self, issue_id: str, pull_request_url: str) -> None:
        response = self._post(
            f'/api/issues/{issue_id}/comments',
            json={'text': f'Pull request created: {pull_request_url}'},
        )
        response.raise_for_status()

    def _to_task(self, payload: dict[str, Any]) -> Task:
        issue_id = payload['idReadable']
        comments = self._get_issue_comments(issue_id)
        attachments = self._get_issue_attachments(issue_id)
        return Task(
            id=issue_id,
            summary=payload.get(Task.summary.key, ''),
            description=self._build_task_description(
                payload.get(Task.description.key) or '',
                comments,
                attachments,
            ),
            branch_name=f'feature/{issue_id.lower()}',
        )

    @staticmethod
    def _build_assigned_tasks_query(project: str, assignee: str, states: list[str]) -> str:
        if not states:
            raise ValueError('states must not be empty')
        state_filter = ', '.join(f'{{{state}}}' for state in states)
        return f'project: {project} assignee: {assignee} State: {state_filter}'

    def _get_issue_comments(self, issue_id: str) -> list[dict[str, Any]]:
        try:
            response = self._get_with_retry(
                f'/api/issues/{issue_id}/comments',
                params={'fields': self.COMMENT_FIELDS},
            )
            response.raise_for_status()
            return list(response.json() or [])
        except Exception:
            return []

    def _get_issue_attachments(self, issue_id: str) -> list[dict[str, Any]]:
        try:
            response = self._get_with_retry(
                f'/api/issues/{issue_id}/attachments',
                params={'fields': self.ATTACHMENT_FIELDS},
            )
            response.raise_for_status()
            return list(response.json() or [])
        except Exception:
            return []

    def _build_task_description(
        self,
        description: str,
        comments: list[dict[str, Any]],
        attachments: list[dict[str, Any]],
    ) -> str:
        sections = [description.strip() or 'No description provided.']

        comment_lines = self._format_comments(comments)
        if comment_lines:
            sections.append('Issue comments:\n' + '\n'.join(comment_lines))

        text_attachment_lines = self._format_text_attachments(attachments)
        if text_attachment_lines:
            sections.append('Text attachments:\n' + '\n\n'.join(text_attachment_lines))

        screenshot_lines = self._format_screenshot_attachments(attachments)
        if screenshot_lines:
            sections.append('Screenshot attachments:\n' + '\n'.join(screenshot_lines))

        return '\n\n'.join(section for section in sections if section)

    @staticmethod
    def _format_comments(comments: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            text = (comment.get('text') or '').strip()
            if not text:
                continue
            author = comment.get('author') or {}
            author_name = author.get('name') or author.get('login') or 'unknown'
            lines.append(f'- {author_name}: {text}')
        return lines

    def _format_text_attachments(self, attachments: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            if not self._is_text_attachment(attachment):
                continue
            content = self._read_text_attachment(attachment)
            if content is None:
                lines.append(
                    f'Attachment {attachment.get("name", "unknown")} could not be downloaded.'
                )
                continue
            if not content:
                continue
            lines.append(f'Attachment {attachment.get("name", "unknown")}:\n{content}')
        return lines

    @staticmethod
    def _format_screenshot_attachments(attachments: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            mime_type = attachment.get('mimeType') or ''
            if not mime_type.startswith('image/'):
                continue
            metadata = attachment.get('metaData') or 'no metadata'
            url = attachment.get('url') or ''
            lines.append(f'- {attachment.get("name", "unknown")} ({metadata}) {url}'.strip())
        return lines

    def _read_text_attachment(self, attachment: dict[str, Any]) -> str | None:
        url = attachment.get('url')
        if not url:
            return ''

        try:
            response = self._get_with_retry(url)
            response.raise_for_status()
            content = getattr(response, 'text', '')
            if isinstance(content, str) and content:
                return content[: self.MAX_TEXT_ATTACHMENT_CHARS]

            raw_content = getattr(response, 'content', b'')
            if not raw_content:
                return ''

            charset = attachment.get('charset') or 'utf-8'
            return raw_content.decode(charset, errors='replace')[: self.MAX_TEXT_ATTACHMENT_CHARS]
        except Exception:
            return None

    @staticmethod
    def _is_text_attachment(attachment: dict[str, Any]) -> bool:
        mime_type = attachment.get('mimeType') or ''
        return mime_type.startswith('text/') or mime_type in {
            'application/json',
            'application/xml',
            'application/yaml',
        }

    def _get_with_retry(self, path: str, **kwargs):
        last_response = None
        for attempt in range(self.max_retries):
            try:
                response = self._get(path, **kwargs)
            except Exception as exc:
                if attempt == self.max_retries - 1 or not is_retryable_exception(exc):
                    raise
                continue

            last_response = response
            if attempt < self.max_retries - 1 and is_retryable_response(response):
                continue
            return response

        return last_response
