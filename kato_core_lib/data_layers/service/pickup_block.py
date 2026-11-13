"""Clear what would stop a kato from picking a ticket up again.

A run leaves kato's comments on the ticket ("started working on this task",
"stopped working on this task: …"), and every kato's pickup reads them as "this
task was already taken" (``active_execution_blocking_comment``). A ticket handed
to someone else, or released again by the plan queue, must not be blocked by
them. THE one place both do it:

* ``delete_own`` — delete the blocking comments THIS tracker account wrote
  (never a person's comment: the author must be the account kato works as, and
  the text must read as a blocking comment);
* ``note`` — when something still blocks, post the operator's override
  (``kato: retry approved …``), which every kato version honours: an override
  posted after a blocking comment clears it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kato_core_lib.data_layers.data.fields import TaskCommentFields
from kato_core_lib.helpers.agent_comment_classification import (
    active_execution_blocking_comment,
    is_execution_blocking_comment,
)


@dataclass(frozen=True)
class PickupUnblock(object):
    deleted: tuple[str, ...] = ()     # the comment ids deleted
    noted: bool = False               # the override note was posted
    problems: tuple[str, ...] = field(default_factory=tuple)


def pickup_blocking_comment(comments) -> str:
    """The comment that still blocks a pickup ('' = none), from ``comments``
    newest first (as ``list_comments`` answers)."""
    oldest_first = [{TaskCommentFields.BODY: comment.body} for comment in reversed(list(comments))]
    return active_execution_blocking_comment(oldest_first)


def clear_pickup_block(tracker, task_service, task_id: str, *, delete_own: bool, note: str = '') -> PickupUnblock:
    comments = list(tracker.list_comments(task_id))
    if not pickup_blocking_comment(comments):
        return PickupUnblock()
    deleted: list[str] = []
    problems: list[str] = []
    if delete_own:
        deleted, problems = _delete_own_blockers(tracker, task_id, comments)
        comments = [comment for comment in comments if comment.comment_id not in deleted]
    if not note or not pickup_blocking_comment(comments):
        return PickupUnblock(tuple(deleted), False, tuple(problems))
    task_service.add_comment(task_id, note)
    return PickupUnblock(tuple(deleted), True, tuple(problems))


def _delete_own_blockers(tracker, task_id: str, comments) -> tuple[list[str], list[str]]:
    try:
        me = str(tracker.current_user() or '').strip().lower()
    except Exception as exc:
        return [], [f'could not tell which comments are kato\'s own: {exc}']
    if not me:
        return [], ['could not tell which comments are kato\'s own: the tracker named no account']
    deleted: list[str] = []
    problems: list[str] = []
    for comment in comments:
        if str(comment.author or '').strip().lower() != me or not is_execution_blocking_comment(comment.body):
            continue
        try:
            tracker.delete_comment(task_id, comment.comment_id)
        except Exception as exc:
            problems.append(f'could not delete kato\'s comment {comment.comment_id}: {exc}')
            continue
        deleted.append(comment.comment_id)
    return deleted, problems
