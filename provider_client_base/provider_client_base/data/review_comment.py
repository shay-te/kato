from __future__ import annotations


class ReviewComment(object):
    def __init__(
        self,
        pull_request_id: str = '',
        comment_id: str = '',
        author: str = '',
        body: str = '',
        file_path: str = '',
        line_number: int | str = '',
        line_type: str = '',
        commit_sha: str = '',
        author_id: str = '',
    ) -> None:
        self.pull_request_id = pull_request_id
        self.comment_id = comment_id
        self.author = author
        # The author's STABLE handle (bitbucket nickname/account_id, github
        # login, gitlab username) — as opposed to ``author``, which providers
        # may render as a human display name.
        #
        # A host identifies its own bot by handle, so an identity check that
        # only ever saw ``author`` compared a display name ("Jane Doe")
        # against a configured handle ("jane.doe") on Bitbucket and could
        # never match. A host that cannot recognise its OWN replies re-polls
        # them as fresh reviewer comments and reposts the same reply on every
        # poll — an unbounded self-reply loop.
        self.author_id = author_id
        self.body = body
        self.file_path = file_path
        self.line_number = line_number
        self.line_type = line_type
        self.commit_sha = commit_sha

    def __repr__(self) -> str:
        return (
            'ReviewComment('
            f'pull_request_id={self.pull_request_id!r}, '
            f'comment_id={self.comment_id!r}, '
            f'author={self.author!r}, '
            f'body={self.body!r}, '
            f'file_path={self.file_path!r}, '
            f'line_number={self.line_number!r}, '
            f'line_type={self.line_type!r}, '
            f'commit_sha={self.commit_sha!r})'
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ReviewComment):
            return False
        return (
            self.pull_request_id == other.pull_request_id
            and self.comment_id == other.comment_id
            and self.author == other.author
            and self.body == other.body
            and self.file_path == other.file_path
            and self.line_number == other.line_number
            and self.line_type == other.line_type
            and self.commit_sha == other.commit_sha
        )
