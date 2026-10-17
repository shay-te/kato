class PullRequestFields(object):
    ID = 'id'
    TITLE = 'title'
    URL = 'url'
    SOURCE_BRANCH = 'source_branch'
    DESTINATION_BRANCH = 'destination_branch'
    DESCRIPTION = 'description'
    REPOSITORY_ID = 'repository_id'
    PULL_REQUESTS = 'pull_requests'
    FAILED_REPOSITORIES = 'failed_repositories'
    STATE = 'state'
    CREATED_AT = 'created_at'


class PullRequestStates(object):
    """A pull request's normalized lifecycle state, plus the lookup filter.

    A normalized pull request's ``state`` is OPEN, MERGED or DECLINED (closed
    without merging), whatever the provider calls them; ``''`` when the
    provider sent a state it does not map, so an unknown state is never read
    as merged. ALL is a lookup filter only: ``find_pull_requests(state=ALL)``
    asks for every state where the default asks for open ones.
    """
    OPEN = 'open'
    MERGED = 'merged'
    DECLINED = 'declined'
    ALL = 'all'
    LOOKUP_FILTERS = (OPEN, ALL)


class ReviewCommentFields(object):
    PULL_REQUEST_ID = 'pull_request_id'
    COMMENT_ID = 'comment_id'
    AUTHOR = 'author'
    BODY = 'body'
    ALL_COMMENTS = 'all_comments'
    RESOLUTION_TARGET_ID = 'resolution_target_id'
    RESOLUTION_TARGET_TYPE = 'resolution_target_type'
    RESOLVABLE = 'resolvable'
    FILE_PATH = 'file_path'
    LINE_NUMBER = 'line_number'
    LINE_TYPE = 'line_type'
    COMMIT_SHA = 'commit_sha'
