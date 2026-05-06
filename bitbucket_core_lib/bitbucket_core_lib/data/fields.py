from __future__ import annotations


class BitbucketIssueFields(object):
    ID = 'id'
    TITLE = 'title'
    CONTENT = 'content'
    RAW = 'raw'
    STATE = 'state'
    ASSIGNEE = 'assignee'
    LABELS = 'labels'
    DISPLAY_NAME = 'display_name'
    NICKNAME = 'nickname'


class BitbucketIssueCommentFields(object):
    CONTENT = 'content'
    RAW = 'raw'
    USER = 'user'
    DISPLAY_NAME = 'display_name'
    NICKNAME = 'nickname'


ISSUE_COMMENT_AUTHOR = 'author'
ISSUE_COMMENT_BODY = 'body'
ISSUE_ALL_COMMENTS = 'all_comments'
