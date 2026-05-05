from __future__ import annotations

from dataclasses import dataclass

from kato_core_lib.data_layers.data.review_comment import ReviewComment
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
    StatusFields,
    TaskFields,
)
from kato_core_lib.helpers.task_execution_utils import task_execution_report
from kato_core_lib.helpers.text_utils import normalized_text, text_from_mapping

KATO_REVIEW_COMMENT_FIXED_PREFIX = 'Kato addressed review comment '
KATO_REVIEW_COMMENT_REPLY_PREFIX = 'Kato addressed this review comment'


@dataclass(frozen=True)
class ReviewFixContext(object):
    repository_id: str
    branch_name: str
    session_id: str
    task_id: str
    task_summary: str
    pull_request_title: str


def review_comment_from_payload(payload: dict) -> ReviewComment:
    try:
        comment = ReviewComment(
            pull_request_id=str(payload[ReviewCommentFields.PULL_REQUEST_ID]),
            comment_id=str(payload[ReviewCommentFields.COMMENT_ID]),
            author=str(payload[ReviewCommentFields.AUTHOR]),
            body=str(payload[ReviewCommentFields.BODY]),
            file_path=str(payload.get(ReviewCommentFields.FILE_PATH, '') or ''),
            line_number=_coerce_optional_int(
                payload.get(ReviewCommentFields.LINE_NUMBER, ''),
            ),
            line_type=str(payload.get(ReviewCommentFields.LINE_TYPE, '') or ''),
            commit_sha=str(payload.get(ReviewCommentFields.COMMIT_SHA, '') or ''),
        )
        if PullRequestFields.REPOSITORY_ID in payload:
            setattr(
                comment,
                PullRequestFields.REPOSITORY_ID,
                str(payload[PullRequestFields.REPOSITORY_ID]),
            )
        setattr(
            comment,
            ReviewCommentFields.ALL_COMMENTS,
            normalize_comment_context(payload.get(ReviewCommentFields.ALL_COMMENTS, [])),
        )
        return comment
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f'invalid review comment payload: {exc}') from exc


def _coerce_optional_int(value: object) -> int | str:
    if value is None or value == '':
        return ''
    try:
        n = int(value)
    except (TypeError, ValueError):
        return ''
    if n <= 0:
        return ''
    return n


def comment_context_entry(comment: ReviewComment) -> dict[str, str]:
    return {
        ReviewCommentFields.COMMENT_ID: str(comment.comment_id),
        ReviewCommentFields.AUTHOR: str(comment.author),
        ReviewCommentFields.BODY: str(comment.body),
    }


def review_comment_resolution_key(comment: ReviewComment) -> tuple[str, str]:
    resolution_target_type = str(
        getattr(comment, ReviewCommentFields.RESOLUTION_TARGET_TYPE, '') or 'comment'
    ).strip() or 'comment'
    resolution_target_id = str(
        getattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID, '')
        or comment.comment_id
        or ''
    ).strip()
    return resolution_target_type, resolution_target_id


def review_comment_processing_keys(comment: ReviewComment) -> set[str]:
    keys = {normalized_text(comment.comment_id)}
    resolution_target_type, resolution_target_id = review_comment_resolution_key(comment)
    if resolution_target_id:
        keys.add(f'{resolution_target_type}:{resolution_target_id}')
    return {key for key in keys if key}


def is_kato_review_comment_reply(comment: ReviewComment) -> bool:
    body = normalized_text(comment.body)
    return body.startswith(
        (
            KATO_REVIEW_COMMENT_FIXED_PREFIX,
            KATO_REVIEW_COMMENT_REPLY_PREFIX,
        )
    )


# Heuristic: question vs fix-request classification for review comments.
#
# Why this exists: review comments like "how will this work when X?"
# are questions, not fix requests. Today kato treats every comment as
# a fix request — pushes a "follow-up update" reply even when nothing
# changed. ``is_question_comment`` lets the service route pure-question
# batches through an answer-only flow: agent reads the code, replies
# with a plain-text answer, no commit, no push.
#
# Conservative by design: false-positive cost is high (a fix request
# misclassified as a question gets *no* fix, just a chat reply), so
# the rule defaults to "fix" on anything ambiguous. The reviewer's
# wording has to look unambiguously like a question for the answer
# flow to fire.

# Question must end with ``?`` and start with one of these words.
_QUESTION_START_WORDS = (
    'how', 'why', 'what', 'when', 'where', 'who', 'which',
    'could', 'can', 'will', 'would', 'should', 'is', 'are', 'do',
    'does', 'did', 'have', 'has', 'any reason', 'curious',
)
# Imperative-leaning words that disqualify even a ?-ending comment
# from the answer flow. Catches phrasing like "should this be a
# constant?" / "shouldn't we use X?" — those read as fix requests
# despite the question mark.
_FIX_REQUEST_WORDS = (
    'fix', 'rename', 'extract', 'remove', 'delete', 'add',
    'use a constant', 'use the constant',
    'should be', 'should use', 'shouldn\'t this', 'should this',
    'shouldn\'t we', 'should we',
    'needs to', 'need to',
    'change this', 'move this', 'replace this',
    'make this', 'make it',
)
# Cap on body length. Long comments are rarely pure questions; the
# reviewer usually buries a fix request inside the explanation.
_QUESTION_MAX_LENGTH = 400


def is_question_comment(comment: ReviewComment) -> bool:
    """True when ``comment.body`` looks unambiguously like a question.

    Conservative — every check has to pass. Returns False on any
    ambiguity so kato defaults to fix-mode (today's behaviour) for
    anything the heuristic can't confidently classify as a question.
    """
    body = str(getattr(comment, 'body', '') or '').strip()
    if not body:
        return False
    if not body.endswith('?'):
        return False
    if len(body) > _QUESTION_MAX_LENGTH:
        return False
    lowered = body.lower()
    if not lowered.startswith(_QUESTION_START_WORDS):
        return False
    if any(token in lowered for token in _FIX_REQUEST_WORDS):
        return False
    return True


def is_question_only_batch(comments) -> bool:
    """True when every comment in ``comments`` looks like a question.

    Used by the service to decide between fix-mode and answer-mode
    for the whole batch. Mixed batches stay on fix-mode — splitting
    the batch into two agent spawns would erase the batching
    efficiency for marginal benefit.
    """
    comments = list(comments or [])
    if not comments:
        return False
    return all(is_question_comment(c) for c in comments)


def review_comment_fixed_comment(comment: ReviewComment) -> str:
    return (
        f'{KATO_REVIEW_COMMENT_FIXED_PREFIX}{comment.comment_id} '
        f'on pull request {comment.pull_request_id}.'
    )


def review_comment_reply_body(execution: dict[str, str | bool]) -> str:
    report = task_execution_report(execution).strip()
    if not report:
        return 'Kato addressed this review comment and pushed a follow-up update.'
    return (
        'Kato addressed this review comment and pushed a follow-up update.\n\n'
        f'{report}'
    )


def review_comment_answer_body(execution: dict[str, str | bool]) -> str:
    """Build the reply body for an answer-mode review comment.

    Pulls the agent's plain-text answer out of the execution dict.
    Reply text is what the reviewer reads — kato's own
    "addressed/pushed" template would be misleading here because no
    code changed and nothing was pushed.

    Different backends populate different fields:
    - Claude one-shot puts the final text in ``result``.
    - OpenHands / Claude streaming put it in ``message``.
    - Some implementations put it in ``summary``.
    Try them in priority order; first non-empty wins.
    """
    for key in ('message', 'result', ImplementationFields.MESSAGE, 'summary'):
        value = str(execution.get(key) or '').strip()
        if value:
            return value
    return (
        'Kato read this question but did not produce an answer. '
        'Re-open the thread for a fresh attempt.'
    )


def normalize_comment_context(all_comments) -> list[dict[str, str]]:
    if not isinstance(all_comments, list):
        return []

    normalized_comments: list[dict[str, str]] = []
    for item in all_comments:
        if isinstance(item, ReviewComment):
            normalized_comments.append(
                {
                    ReviewCommentFields.COMMENT_ID: str(item.comment_id),
                    ReviewCommentFields.AUTHOR: str(item.author),
                    ReviewCommentFields.BODY: str(item.body),
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        normalized_comment = {
            ReviewCommentFields.COMMENT_ID: str(item.get(ReviewCommentFields.COMMENT_ID, '')),
            ReviewCommentFields.AUTHOR: str(item.get(ReviewCommentFields.AUTHOR, '')),
            ReviewCommentFields.BODY: str(item.get(ReviewCommentFields.BODY, '')),
        }
        if not any(normalized_comment.values()):
            continue
        normalized_comments.append(normalized_comment)
    return normalized_comments


def review_fix_context_from_mapping(context: dict[str, str]) -> ReviewFixContext:
    return ReviewFixContext(
        repository_id=text_from_mapping(context, PullRequestFields.REPOSITORY_ID),
        branch_name=text_from_mapping(context, Task.branch_name.key),
        session_id=text_from_mapping(context, ImplementationFields.SESSION_ID),
        task_id=text_from_mapping(context, TaskFields.ID),
        task_summary=text_from_mapping(context, TaskFields.SUMMARY),
        pull_request_title=text_from_mapping(context, PullRequestFields.TITLE),
    )


def review_fix_result(
    comment: ReviewComment,
    review_context: ReviewFixContext,
) -> dict[str, str]:
    return {
        StatusFields.STATUS: StatusFields.UPDATED,
        ReviewCommentFields.PULL_REQUEST_ID: comment.pull_request_id,
        Task.branch_name.key: review_context.branch_name,
        PullRequestFields.REPOSITORY_ID: review_context.repository_id,
    }
