"""Operator kill switch for pulling PR review comments from the git host.

``KATO_REVIEW_COMMENTS_ENABLED`` (default ``true``) gates the whole
review-comment loop. When it is off:

* kato stops asking Bitbucket / GitHub / GitLab for new pull-request
  comments (no polling call is made at all), and
* a review-fix batch that was already dispatched refuses to start, and
* the run already in flight is terminated (see
  ``ReviewCommentService.stop_active_review_comment_work``).

Read FRESH on every check — same reasoning and same store precedence as
:mod:`kato_core_lib.helpers.action_guard_config`: ``~/.kato/settings.json``
is the live, UI-managed source and ``os.environ`` is the fallback for a key
only ever set in the shell. Resolving this through the boot-time OmegaConf
config instead would make it a restart-only setting, which is exactly what
it must not be — the operator flips it to stop kato *now*.

Scope is deliberately narrow: PR review comments pulled from a git host.
Ticket comments have their own switch (``KATO_TASK_COMMENTS_ENABLED``) and
the diff comments an operator writes in kato's own Files tab aren't pulled
from anywhere, so neither is affected by this key.
"""

from __future__ import annotations

import os

from kato_core_lib.helpers.kato_settings_store_utils import read_kato_settings

REVIEW_COMMENTS_ENABLED_KEY = 'KATO_REVIEW_COMMENTS_ENABLED'

# Unset means ON — this is an opt-OUT, so an operator who never touches the
# switch keeps the behaviour kato has always had.
REVIEW_COMMENTS_ENABLED_DEFAULT = 'true'

# Logged / replied wherever the gate turns something away, so "kato stopped
# answering my PR comments" is one grep away from its cause.
REVIEW_COMMENTS_DISABLED_REASON = (
    'pull-request review comments are switched off '
    f'({REVIEW_COMMENTS_ENABLED_KEY}=false)'
)

_FALSY = ('false', '0', 'no', 'off')


def _resolved_value(env: dict) -> str:
    """settings.json (live) → shell env → default-on."""
    try:
        settings = read_kato_settings()
    except Exception:  # noqa: BLE001 - a corrupt settings file must not
        settings = {}  # decide the switch; fall through to env/default.
    for source in (settings, env):
        value = source.get(REVIEW_COMMENTS_ENABLED_KEY)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return REVIEW_COMMENTS_ENABLED_DEFAULT


def review_comments_enabled(env: dict | None = None) -> bool:
    """``False`` only when the operator explicitly switched the loop off."""
    env = os.environ if env is None else env
    return _resolved_value(env).lower() not in _FALSY


REVIEW_COMMENTS_REQUIRE_MENTION_KEY = 'KATO_REVIEW_COMMENTS_REQUIRE_MENTION'

# Default ON: a pull-request comment is a conversation between reviewers by
# default, and only the ones that actually tag kato are addressed to it.
REVIEW_COMMENTS_REQUIRE_MENTION_DEFAULT = 'true'

_TRUTHY = ('true', '1', 'yes', 'on')


def _resolved_require_mention(env: dict) -> str:
    """settings.json (live) → shell env → default-on. Mirrors the gate above."""
    try:
        settings = read_kato_settings()
    except Exception:  # noqa: BLE001 - a corrupt settings file must not
        settings = {}  # decide the switch; fall through to env/default.
    for source in (settings, env):
        value = source.get(REVIEW_COMMENTS_REQUIRE_MENTION_KEY)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return REVIEW_COMMENTS_REQUIRE_MENTION_DEFAULT


def review_comments_require_mention(env: dict | None = None) -> bool:
    """Must a PR review comment ``@mention`` kato for kato to act on it?

    ``True`` (default) means kato answers ONLY comments that tag it, and
    ignores everything else on the pull request — including comments that tag
    nobody, which are reviewers talking to each other.

    ``False`` restores the older, looser rule: act on everything except
    comments that tag a human other than kato.

    Read fresh on every check, same as
    :func:`review_comments_enabled` — the operator changes this to change
    kato's behaviour now, not after a restart.
    """
    env = os.environ if env is None else env
    return _resolved_require_mention(env).lower() in _TRUTHY
