class PullRequestFields:
    ID = 'id'
    TITLE = 'title'
    URL = 'url'
    SOURCE_BRANCH = 'source_branch'
    DESTINATION_BRANCH = 'destination_branch'
    DESCRIPTION = 'description'


class ImplementationFields:
    COMMIT_MESSAGE = 'commit_message'
    SUCCESS = 'success'


class StatusFields:
    STATUS = 'status'
    UPDATED = 'updated'


class EmailFields:
    EMAIL = 'email'
    SUBJECT = 'subject'
    MESSAGE = 'message'
    OPERATION = 'operation'
    ERROR = 'error'
    CONTEXT = 'context'
    TASK_ID = 'task_id'
    TASK_SUMMARY = 'task_summary'
    PULL_REQUEST_TITLE = 'pull_request_title'
    PULL_REQUEST_URL = 'pull_request_url'


class YouTrackAttachmentFields:
    ID = 'id'
    NAME = 'name'
    MIME_TYPE = 'mimeType'
    CHARSET = 'charset'
    METADATA = 'metaData'
    URL = 'url'


class YouTrackCommentFields:
    ID = 'id'
    TEXT = 'text'
    AUTHOR = 'author'
    LOGIN = 'login'
    NAME = 'name'


class YouTrackCustomFieldFields:
    ID = 'id'
    NAME = 'name'
    TYPE = '$type'
