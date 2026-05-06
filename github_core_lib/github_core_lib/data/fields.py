from __future__ import annotations


class GitHubIssueFields(object):
    NUMBER = 'number'
    TITLE = 'title'
    BODY = 'body'
    STATE = 'state'
    LABELS = 'labels'
    ASSIGNEES = 'assignees'
    LOGIN = 'login'
    PULL_REQUEST = 'pull_request'
    NAME = 'name'


class GitHubCommentFields(object):
    BODY = 'body'
    USER = 'user'
    LOGIN = 'login'


ISSUE_COMMENT_AUTHOR = 'author'
ISSUE_COMMENT_BODY = 'body'
ISSUE_ALL_COMMENTS = 'all_comments'
