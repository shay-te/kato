"""Shared @-mention filter for ticket-platform comments.

Every ticket platform (YouTrack, Jira, GitHub Issues, GitLab Issues,
Bitbucket Issues) pulls comments off the issue and embeds them in the
task description that ultimately reaches the agent. Without filtering,
a comment like ``@jane.doe please look at this`` becomes work the
agent attempts — even though it was clearly directed at a human, not
the kato bot user. This module is the one helper every platform's
client calls to decide whether a given comment is "for someone else".

Single rule:

  * Comment contains at least one ``@login`` mention AND none of
    those mentions match the configured bot login  →  skip.
  * Otherwise (no mentions OR a mention that DOES match the bot)
    →  include.

Bot login defaults to ``""`` and the YouTrack alias ``"me"`` is also
treated as unset — both turn the filter into a no-op so platforms
that haven't configured a real bot login preserve their pre-filter
behavior.
"""
from __future__ import annotations

import re


# ``@login`` at a word boundary. Login characters cover the union of
# what YouTrack / Jira / GitHub / GitLab / Bitbucket accept:
# letters, digits, underscore, dot, hyphen.
#
# * The lookbehind on ``[\w.]`` keeps email addresses like
#   ``foo@example.com`` from matching ``@example``.
# * The login must start AND end with a word character so that
#   sentence punctuation like ``@carol.`` doesn't get consumed as
#   part of the login. Internal ``.`` / ``-`` (e.g. ``@user.name``,
#   ``@bob-jr``) is still allowed.
_MENTION_PATTERN = re.compile(r'(?<![\w.])@(\w(?:[\w.\-]*\w)?)')


def extract_mention_logins(body: object) -> list[str]:
    """Return lowercase logins mentioned in ``body`` via ``@login``.

    Returns ``[]`` when ``body`` is falsy, not a string, or contains
    no recognizable mentions. The result preserves source order but
    lowercases each login so the host can do a case-insensitive
    comparison against its configured bot login.
    """
    if not body:
        return []
    text = body if isinstance(body, str) else str(body)
    return [m.group(1).lower() for m in _MENTION_PATTERN.finditer(text)]


def _normalize_bot_login(bot_login: object) -> str:
    """Lowercase and strip the bot login; treat ``"me"`` as unset.

    YouTrack accepts ``"me"`` as an alias for "the calling user" in
    queries, but it never appears as a literal mention in comment
    bodies — so a ``"me"`` value can never match and must be treated
    as "filter disabled".
    """
    if bot_login is None:
        return ''
    text = str(bot_login).strip().lower()
    return '' if text == 'me' else text


def _as_login_candidates(bot_logins: object) -> tuple:
    """Normalize the ``bot_logins`` argument to a tuple of candidates.

    A bare string (or ``None``) is treated as a single login; any other
    iterable is taken as-is.
    """
    if bot_logins is None or isinstance(bot_logins, str):
        return (bot_logins,)
    return tuple(bot_logins)


def is_addressed_elsewhere_from_mentions(
    mention_ids: object, bot_logins: object
) -> bool:
    """Apply the single rule to ALREADY-EXTRACTED mention identities.

    The ``@login`` text form is only one way a platform encodes a
    mention. Jira embeds mentions as ADF nodes keyed by ``accountId``;
    Bitbucket writes ``@{account_id}``. Those clients extract their own
    mention identities and call this directly, so the "mentions humans
    other than the bot" rule and its normalization live in exactly one
    place rather than being re-implemented per platform.

    ``mention_ids`` is any iterable of strings (account ids, usernames,
    display handles). ``bot_logins`` is one login or an iterable of the
    bot's known logins/ids. Empty / ``"me"`` bot logins are ignored, so a
    bot with no usable identity disables the filter (returns False).
    """
    mentions = {
        str(mention).strip().lower()
        for mention in (mention_ids or ())
        if str(mention).strip()
    }
    if not mentions:
        return False
    logins = {_normalize_bot_login(candidate)
              for candidate in _as_login_candidates(bot_logins)}
    logins.discard('')
    if not logins:
        return False
    return logins.isdisjoint(mentions)


def is_comment_addressed_elsewhere_any(body: object, bot_logins: object) -> bool:
    """Same rule as :func:`is_comment_addressed_elsewhere`, but for a bot
    known under SEVERAL logins at once.

    A bot can have more than one login simultaneously — e.g. its
    ticket-platform ``assignee`` and its (often different) code-host
    username. A comment that ``@mentions`` the bot under ANY of those logins
    is "for the bot" and is kept; only a comment that mentions other people
    and none of the bot's logins is skipped. Empty / ``"me"`` logins are
    ignored, so a bot with no usable login disables the filter (returns
    False), exactly like the single-login form. A bare string is accepted as
    a single login.

    For the plain ``@login`` text encoding; platforms with a different
    mention encoding extract identities themselves and call
    :func:`is_addressed_elsewhere_from_mentions`.
    """
    return is_addressed_elsewhere_from_mentions(
        extract_mention_logins(body), bot_logins
    )


def is_comment_addressed_elsewhere(body: object, bot_login: object) -> bool:
    """Is this comment @-mentioning humans OTHER than the bot user?

    See the module docstring for the rule. Returns False whenever
    the filter is disabled (empty / ``"me"`` bot login) so callers
    can wire this in unconditionally. Thin single-login wrapper over
    :func:`is_comment_addressed_elsewhere_any`.
    """
    return is_comment_addressed_elsewhere_any(body, (bot_login,))
