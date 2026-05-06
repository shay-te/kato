"""Field-name constants for YouTrack API responses and task comments."""


class TaskCommentFields:
    AUTHOR = 'author'
    BODY = 'body'
    ALL_COMMENTS = 'all_comments'


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


class YouTrackTagFields:
    NAME = 'name'
