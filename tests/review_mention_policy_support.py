"""Pin the PR review-comment mention policy for tests that predate it.

``KATO_REVIEW_COMMENTS_REQUIRE_MENTION`` defaults ON: kato acts only on
pull-request comments that ``@mention`` it. That is a POLICY, and most of the
review-comment suite is not about policy — it covers dedup, chronological
order, reopen semantics, and the fix pipeline, using plain reviewer comments
that tag nobody.

Rather than sprinkle an ``@bot`` into fixtures that have nothing to do with
mentions (which would hide what those tests actually assert), those modules
pin the policy to the legacy rule and let the dedicated suites
— ``test_review_comment_require_mention`` — own the default.

Patches the name where it is USED. ``review_comment_service`` does
``from ... import review_comments_require_mention``, so patching the helper's
own module would rebind a name nothing reads.
"""

from unittest.mock import patch

from kato_core_lib.data_layers.service import review_comment_service


def legacy_mention_policy():
    """Context manager / patcher: act on comments that tag nobody."""
    return patch.object(
        review_comment_service,
        'review_comments_require_mention',
        return_value=False,
    )
