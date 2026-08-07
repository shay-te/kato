"""Classify a ticket comment as one kato's own agent posted.

Kato writes operational comments onto the ticket as it works ("started working
on this task", "completed task X", "could not safely process this task"). The
pre-flight path has to read them back to answer three questions:

  * did a previous run finish this task?              -> is_completion_comment
  * is a prior refusal still blocking a fresh start?  -> is_pre_start_blocking_comment
  * is a run currently in flight / blocked?           -> active_execution_blocking_comment

This lived on ``TicketClientBase``, a 462-line HTTP client class with **zero
subclasses** whose entire transport surface (HTTP verbs, pagination, attachment
download, comment-entry building) duplicated
``provider_client_base.client.issue_client_base.IssueClientBase`` — the class
every real provider client actually extends. 34 of its 37 members had no
production caller at all; these three were the only reason it was still
imported, so they are extracted here and the dead class is deleted.

The prefixes are kato's own brand strings, so this belongs in kato_core_lib and
must never move into a core-lib.
"""

from __future__ import annotations

from kato_core_lib.data_layers.data.fields import TaskCommentFields
from utils_core_lib.utils_core_lib.text_utils import (
    condensed_lower_text,
    normalized_text,
    text_from_mapping,
)


AGENT_COMPLETION_COMMENT_PREFIX = 'Kato completed task '

#: A refusal posted BEFORE any work started. Still present on the ticket means
#: the same blocking condition is presumed unresolved, so a re-scan skips the
#: task rather than retrying into the same wall.
PRE_START_BLOCKING_PREFIXES = (
    'Kato agent could not safely process this task:',
    'Kato agent skipped this task because it could not detect which repository',
    'Kato agent skipped this task because the task definition',
)

#: Every operational comment shape kato posts — used to tell its own writing
#: apart from a human's when scanning a ticket thread.
AGENT_COMMENT_PREFIXES = (
    *PRE_START_BLOCKING_PREFIXES,
    'Kato agent started working on this task',
    'Kato agent stopped working on this task:',
    'Kato addressed review comment ',
    AGENT_COMPLETION_COMMENT_PREFIX,
)

AGENT_RETRY_BLOCKING_PREFIXES = PRE_START_BLOCKING_PREFIXES + (
    'Kato agent stopped working on this task:',
)

AGENT_EXECUTION_BLOCKING_PREFIXES = AGENT_RETRY_BLOCKING_PREFIXES + (
    AGENT_COMPLETION_COMMENT_PREFIX,
    'Kato agent started working on this task',
)

#: The operator's explicit "go again" override. Posting it AFTER a blocking
#: comment clears that block, which is why the scan below is order-sensitive.
RETRY_OVERRIDE_COMMAND_PREFIXES = (
    'kato: retry approved',
    'kato retry approved',
)


def _matches_prefixes(text: object, prefixes: tuple[str, ...]) -> bool:
    normalized_value = normalized_text(text)
    return any(normalized_value.startswith(prefix) for prefix in prefixes)


def is_agent_operational_comment(text: object) -> bool:
    """True when kato itself posted this comment, judged by its wording."""
    return _matches_prefixes(text, AGENT_COMMENT_PREFIXES)


def is_agent_authored_comment(
    comment: object,
    bot_identities: object = (),
) -> bool:
    """True when kato wrote this ticket comment — by ACCOUNT first, wording second.

    ``comment`` is a comment entry (``{author, author_id, body}``).

    Account beats wording. Wording is a guess that breaks every time the text
    changes or a human quotes kato back at it; the authoring account is a fact.
    This is what a dedicated kato user buys: give kato its own ticket account
    and ``author_id`` answers "did I write this?" outright, no prefix matching.

    ``bot_identities`` is compared against the STABLE handle (``author_id``),
    not the rendered display name — providers send "Jane Doe" where the config
    holds "jane.doe", and comparing those never matches.

    Falls back to the wording check when the account can't decide: no
    identities configured, no ``author_id`` on the entry (an older provider
    path), or the comment was authored by someone else. That last case matters
    for the SHARED-account setup, where kato posts as the operator: the
    account can't distinguish them, so the prefixes still carry the weight.
    """
    identities = {
        str(identity).strip().lower()
        for identity in (bot_identities or ())
        if str(identity).strip()
    }
    author_id = normalized_text(
        text_from_mapping(comment, TaskCommentFields.AUTHOR_ID),
    ).lower()
    if identities and author_id and author_id in identities:
        return True
    return is_agent_operational_comment(
        text_from_mapping(comment, TaskCommentFields.BODY),
    )


def is_completion_comment(text: object) -> bool:
    """True when the comment says a prior run completed the task."""
    return _matches_prefixes(text, (AGENT_COMPLETION_COMMENT_PREFIX,))


def is_pre_start_blocking_comment(text: object) -> bool:
    """True when the comment is a pre-start refusal that should block a retry."""
    return _matches_prefixes(text, PRE_START_BLOCKING_PREFIXES)


def is_retry_override_comment(text: object) -> bool:
    """True when the operator explicitly approved another attempt.

    Kato's OWN comments never count, so a blocking comment that happens to
    contain the phrase cannot clear itself.
    """
    if is_agent_operational_comment(text):
        return False
    normalized_comment = condensed_lower_text(text)
    if not normalized_comment:
        return False
    return any(
        normalized_comment.startswith(prefix)
        for prefix in RETRY_OVERRIDE_COMMAND_PREFIXES
    )


def active_execution_blocking_comment(comments: list[dict[str, str]] | None) -> str:
    """The blocking comment still in force, or '' when nothing blocks.

    Order-sensitive by design: the thread is walked oldest-to-newest, and an
    operator retry-override posted AFTER a blocking comment clears it. A later
    blocking comment re-arms the block.
    """
    return _active_agent_blocking_comment(comments, AGENT_EXECUTION_BLOCKING_PREFIXES)


def _active_agent_blocking_comment(
    comments: list[dict[str, str]] | None,
    blocking_prefixes: tuple[str, ...],
) -> str:
    active_comment = ''
    for comment in comments or []:
        if not isinstance(comment, dict):
            continue
        text = text_from_mapping(comment, TaskCommentFields.BODY)
        if not text:
            continue
        if _matches_prefixes(text, blocking_prefixes):
            active_comment = text
            continue
        if active_comment and is_retry_override_comment(text):
            active_comment = ''
    return active_comment
