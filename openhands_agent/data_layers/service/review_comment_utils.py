from dataclasses import dataclass

from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
    StatusFields,
    TaskFields,
)
from openhands_agent.text_utils import text_from_mapping


@dataclass(frozen=True)
class ReviewFixContext:
    repository_id: str
    branch_name: str
    session_id: str
    task_id: str
    task_summary: str


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


def review_comment_fixed_comment(comment: ReviewComment) -> str:
    return (
        'OpenHands addressed review comment '
        f'{comment.comment_id} on pull request {comment.pull_request_id}.'
    )


def review_fix_context_from_mapping(context: dict[str, str]) -> ReviewFixContext:
    return ReviewFixContext(
        repository_id=text_from_mapping(context, PullRequestFields.REPOSITORY_ID),
        branch_name=text_from_mapping(context, Task.branch_name.key),
        session_id=text_from_mapping(context, ImplementationFields.SESSION_ID),
        task_id=text_from_mapping(context, TaskFields.ID),
        task_summary=text_from_mapping(context, TaskFields.SUMMARY),
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
