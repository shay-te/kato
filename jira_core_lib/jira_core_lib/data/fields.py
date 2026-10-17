from __future__ import annotations

# Re-exported from the shared base so every provider issue client and the
# shared IssueClientBase agree on the neutral comment-entry dict shape.
from provider_client_base.provider_client_base.data.issue_record import (  # noqa: F401
    ISSUE_ALL_COMMENTS,
    ISSUE_COMMENT_AUTHOR,
    ISSUE_COMMENT_BODY,
)


class JiraIssueFields(object):
    KEY = 'key'
    FIELDS = 'fields'
    SUMMARY = 'summary'
    DESCRIPTION = 'description'
    COMMENT = 'comment'
    ATTACHMENT = 'attachment'
    LABELS = 'labels'
    STATUS = 'status'
    PROJECT = 'project'
    ISSUE_TYPE = 'issuetype'
    PARENT = 'parent'
    ASSIGNEE = 'assignee'
    CREATED = 'created'


class JiraCommentFields(object):
    ID = 'id'
    BODY = 'body'
    AUTHOR = 'author'
    CREATED = 'created'
    # A user object (comment author, assignee, ``/myself``).
    ACCOUNT_ID = 'accountId'
    DISPLAY_NAME = 'displayName'


class JiraProjectFields(object):
    KEY = 'key'
    ISSUE_TYPES = 'issueTypes'


class JiraIssueTypeFields(object):
    ID = 'id'
    SUBTASK = 'subtask'


class JiraAttachmentFields(object):
    FILENAME = 'filename'
    MIME_TYPE = 'mimeType'
    CONTENT = 'content'
    SIZE = 'size'


class JiraTransitionFields(object):
    ID = 'id'
    NAME = 'name'
    TO = 'to'
