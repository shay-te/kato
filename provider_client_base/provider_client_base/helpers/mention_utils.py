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

# Brace-encoded mention: Bitbucket Cloud writes ``@{account_id}`` (or
# ``@{uuid}``) in a comment's ``raw`` body. The plain ``@login`` pattern
# above can't see it (the char after ``@`` is ``{``), so a comment that tags
# a teammate on Bitbucket looked mention-free and slipped past the filter.
_BRACE_MENTION_PATTERN = re.compile(r'@\{([^}]+)\}')

# Common code annotations/decorators that use the SAME ``@token`` syntax as
# a human @-mention (Java/Kotlin/C#/TS annotations, Python decorators). A
# review comment discussing code — "this method is missing @Override" /
# "shouldn't this use a @pytest.fixture?" / "add @property here" — is
# extremely common and is NOT directed at a person, but the plain regex
# above can't tell the two apart. Without this, the "a comment that tags
# ANYONE is that person's to answer" rule silently drops ordinary,
# actionable review feedback any time it happens to mention a decorator.
# Lowercased (tokens are compared lowercased) and intentionally scoped to
# widely-recognized annotation/decorator names, not a general dictionary —
# a real human login exactly matching one of these is vanishingly unlikely,
# and the failure mode of NOT filtering (silently dropping real feedback)
# is worse than the failure mode of filtering (a login this rare goes
# unrecognized as a mention, same as any unconfigured bot identity today).
_NON_HUMAN_MENTION_TOKENS = frozenset({
    # Java / Kotlin / C# / TypeScript decorators & annotations
    'override', 'deprecated', 'test', 'test.each', 'before', 'beforeeach',
    'beforeall', 'after', 'aftereach', 'afterall', 'component', 'service',
    'repository', 'controller', 'restcontroller', 'autowired', 'bean',
    'configuration', 'entity', 'table', 'column', 'id', 'requestmapping',
    'getmapping', 'postmapping', 'putmapping', 'deletemapping', 'inject',
    'injectable', 'input', 'output', 'component.decorator', 'nginjectable',
    'suppresswarnings', 'functionalinterface', 'nonnull', 'nullable',
    # Python decorators
    'property', 'staticmethod', 'classmethod', 'dataclass', 'wraps',
    'lru_cache', 'cached_property', 'abstractmethod', 'contextmanager',
    'pytest.fixture', 'pytest.mark', 'fixture', 'patch', 'mock.patch',
})


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
    return [
        token for m in _MENTION_PATTERN.finditer(text)
        if (token := m.group(1).lower()) not in _NON_HUMAN_MENTION_TOKENS
    ]


def extract_all_mention_tokens(body: object) -> list[str]:
    """Every mention token in ``body``, ACROSS encodings.

    Unions the plain ``@login`` form (GitHub/GitLab/YouTrack) with the
    brace ``@{account_id}`` form (Bitbucket Cloud), lowercased and
    de-duplicated (source order preserved). This is the encoding-agnostic
    extractor the review-comment path needs: it can't know up front whether
    a body carries ``@jane`` or ``@{557058:...}``, and it must catch both to
    tell "this comment tags a human" from "mention-free".
    """
    if not body:
        return []
    text = body if isinstance(body, str) else str(body)
    seen: set[str] = set()
    tokens: list[str] = []
    for match in _MENTION_PATTERN.finditer(text):
        token = match.group(1).lower()
        if token in _NON_HUMAN_MENTION_TOKENS:
            continue
        if token not in seen:
            seen.add(token)
            tokens.append(token)
    # Brace-encoded mentions are Bitbucket account IDs/UUIDs — never a code
    # annotation/decorator (those only ever use the plain ``@token`` form
    # above), so the non-human denylist doesn't apply here.
    for match in _BRACE_MENTION_PATTERN.finditer(text):
        token = match.group(1).strip().lower()
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens


def mentions_include_identity(mention_tokens: object, bot_identities: object) -> bool:
    """True when at least one mention token matches a known bot identity.

    Both sides are normalised (lowercased/stripped). Used by the "process
    only if the bot itself is tagged" rule: a comment that tags people is
    for the bot ONLY when one of those tags is the bot.
    """
    identities = {
        str(identity).strip().lower()
        for identity in (bot_identities or ())
        if str(identity).strip()
    }
    if not identities:
        return False
    tokens = {
        str(token).strip().lower()
        for token in (mention_tokens or ())
        if str(token).strip()
    }
    return not identities.isdisjoint(tokens)


# Configured assignee values that are QUERY ALIASES, not real mention handles.
# A platform accepts them when searching for issues ("assign to the calling
# user") but they can never appear as a literal ``@mention`` in a comment, so
# treating one as an identity is worse than having no identity at all: an
# identity set that holds only junk still counts as "non-empty", which flips
# identity-aware callers off their safe empty-set fallback onto a comparison
# that can never match. One definition, so every path drops the same values.
BOT_IDENTITY_ALIASES: frozenset = frozenset({'me', 'currentuser()'})


def normalize_bot_identities(candidates: object) -> tuple[str, ...]:
    """Lowercase/strip identities, drop blanks + query aliases, de-dupe.

    Accepts a bare string or any iterable. Order is preserved so callers that
    care about precedence (primary login before secondary) keep it.

    Every path that builds a bot-identity set should use this: hand-rolled
    copies had each learned about a DIFFERENT subset of the aliases, which is
    how ``currentuser()`` survived normalization on one path and not another.
    """
    if candidates is None or isinstance(candidates, str):
        candidates = (candidates,)
    out: list[str] = []
    for candidate in candidates:
        token = str(candidate or '').strip().lower()
        if token and token not in BOT_IDENTITY_ALIASES and token not in out:
            out.append(token)
    return tuple(out)


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


def is_should_skip_comment_any(body: object, bot_logins: object) -> bool:
    """Same rule as :func:`is_should_skip_comment`, but for a bot
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


def is_should_skip_comment(body: object, bot_login: object) -> bool:
    """Is this comment @-mentioning humans OTHER than the bot user?

    See the module docstring for the rule. Returns False whenever
    the filter is disabled (empty / ``"me"`` bot login) so callers
    can wire this in unconditionally. Thin single-login wrapper over
    :func:`is_should_skip_comment_any`.
    """
    return is_should_skip_comment_any(body, (bot_login,))
