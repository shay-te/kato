from __future__ import annotations


class GitLabIssueFields(object):
    IID = 'iid'
    TITLE = 'title'
    DESCRIPTION = 'description'
    STATE = 'state'
    LABELS = 'labels'
    ASSIGNEES = 'assignees'
    USERNAME = 'username'
    NAME = 'name'


class GitLabCommentFields(object):
    BODY = 'body'
    AUTHOR = 'author'
    USERNAME = 'username'
    NAME = 'name'
    SYSTEM = 'system'


ISSUE_COMMENT_AUTHOR = 'author'
ISSUE_COMMENT_BODY = 'body'
ISSUE_ALL_COMMENTS = 'all_comments'
