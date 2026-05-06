from __future__ import annotations


class JiraIssueFields(object):
    KEY = 'key'
    FIELDS = 'fields'
    SUMMARY = 'summary'
    DESCRIPTION = 'description'
    COMMENT = 'comment'
    ATTACHMENT = 'attachment'
    LABELS = 'labels'
    STATUS = 'status'


class JiraCommentFields(object):
    BODY = 'body'
    AUTHOR = 'author'
    DISPLAY_NAME = 'displayName'


class JiraAttachmentFields(object):
    FILENAME = 'filename'
    MIME_TYPE = 'mimeType'
    CONTENT = 'content'
    SIZE = 'size'


class JiraTransitionFields(object):
    ID = 'id'
    NAME = 'name'
    TO = 'to'


ISSUE_COMMENT_AUTHOR = 'author'
ISSUE_COMMENT_BODY = 'body'
ISSUE_ALL_COMMENTS = 'all_comments'
