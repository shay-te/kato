"""Regression: kato must never reply to its own PR review reply.

The incident: a PR thread filled with dozens of identical

    "Kato ran an agent against this comment but produced no commits and left
     the working tree clean. …"

replies, one per scan tick, each one emailing every watcher.

Mechanism — ``is_kato_review_comment_reply`` gates its prefix match on the
comment's AUTHOR. Bitbucket renders a review comment's author as the
DISPLAY NAME ("Shay Tessler") while the operator configures kato by
username ("shay.te"), so the comparison could never match. Kato failed to
recognise its own reply, re-polled it as a fresh reviewer comment, ran an
agent, produced no changes, and posted the same reply again — forever.

Two independent defences are asserted here, because identity resolution has
now failed twice in production in two different ways (a Jira
``currentUser()`` alias, then this):

1. ``author_id`` carries the stable handle, so the identity check succeeds.
2. ``looks_like_kato_review_comment_reply`` skips anything already shaped
   like a kato reply regardless of authorship, making the loop structurally
   impossible rather than contingent on (1).
"""

import unittest

from provider_client_base.provider_client_base.data.review_comment import ReviewComment

from kato_core_lib.helpers.review_comment_utils import (
    KATO_REVIEW_COMMENT_ANSWER_PREFIX,
    KATO_REVIEW_COMMENT_FIXED_PREFIX,
    KATO_REVIEW_COMMENT_NO_CHANGES_PREFIX,
    is_kato_review_comment_reply,
    looks_like_kato_review_comment_reply,
)

# Byte-for-byte the body from the incident.
NO_CHANGES_BODY = (
    f'{KATO_REVIEW_COMMENT_NO_CHANGES_PREFIX} The comment '
    "has not been resolved — please review the agent's "
    'reasoning in the planning UI and either re-prompt with '
    'more context, edit the file directly, or resolve the '
    'comment yourself if no change is needed.'
)


def _bitbucket_reply(display_name='Shay Tessler', nickname='shay.te'):
    """A reply as Bitbucket reports it: rendered name + stable handle."""
    return ReviewComment(
        pull_request_id='7', comment_id='101',
        author=display_name, author_id=nickname, body=NO_CHANGES_BODY,
    )


class BitbucketDisplayNameTests(unittest.TestCase):
    """The exact shape that produced the loop."""

    def test_own_reply_is_recognised_despite_the_display_name(self):
        # Identities hold the configured username; author holds the display
        # name. Before author_id existed this returned False → loop.
        self.assertTrue(
            is_kato_review_comment_reply(_bitbucket_reply(), ('shay.te',)))

    def test_still_recognised_when_identities_hold_the_display_name(self):
        self.assertTrue(
            is_kato_review_comment_reply(_bitbucket_reply(), ('shay tessler',)))

    def test_account_id_identity_also_matches(self):
        comment = _bitbucket_reply(nickname='557058:abc-123')
        self.assertTrue(
            is_kato_review_comment_reply(comment, ('557058:abc-123',)))

    def test_a_real_reviewer_comment_is_not_mistaken_for_kato(self):
        comment = ReviewComment(
            pull_request_id='7', comment_id='102',
            author='Dana Reviewer', author_id='dana',
            body='This still leaks the connection on the error path.',
        )
        self.assertFalse(is_kato_review_comment_reply(comment, ('shay.te',)))

    def test_impersonated_prefix_is_still_rejected_by_the_author_check(self):
        # The prefixes are public text. Someone else pasting one must NOT be
        # able to mark the thread addressed and bury a reviewer's comment.
        comment = ReviewComment(
            pull_request_id='7', comment_id='103',
            author='Mallory', author_id='mallory',
            body=f'{KATO_REVIEW_COMMENT_FIXED_PREFIX}42',
        )
        self.assertFalse(is_kato_review_comment_reply(comment, ('shay.te',)))

    def test_unresolvable_identity_still_falls_back_to_prefix_only(self):
        self.assertTrue(is_kato_review_comment_reply(_bitbucket_reply(), ()))


class LoopGuardTests(unittest.TestCase):
    """The second defence: never ACT on something shaped like our own reply."""

    def test_no_changes_reply_is_recognised_by_shape_alone(self):
        self.assertTrue(
            looks_like_kato_review_comment_reply(_bitbucket_reply()))

    def test_guard_holds_even_when_the_author_is_unknown(self):
        # This is the whole point: authorship may be unprovable, but replying
        # would still only produce another copy of the same reply.
        comment = ReviewComment(
            pull_request_id='7', comment_id='104', author='', author_id='',
            body=NO_CHANGES_BODY,
        )
        self.assertTrue(looks_like_kato_review_comment_reply(comment))
        self.assertFalse(is_kato_review_comment_reply(comment, ('shay.te',)))

    def test_every_reply_shape_kato_posts_is_covered(self):
        for body in (
            NO_CHANGES_BODY,
            f'{KATO_REVIEW_COMMENT_FIXED_PREFIX}42',
            f'{KATO_REVIEW_COMMENT_ANSWER_PREFIX} Here is why…',
            f'<sub>{KATO_REVIEW_COMMENT_FIXED_PREFIX}42</sub>',
        ):
            with self.subTest(body=body[:40]):
                self.assertTrue(looks_like_kato_review_comment_reply(
                    ReviewComment(body=body)))

    def test_a_reviewer_comment_is_not_skipped_by_the_guard(self):
        # The guard must not eat real feedback — that would be the opposite
        # failure (kato silently ignoring the reviewer).
        for body in ('Please rename this variable.',
                     'Kato, can you look at the retry logic?',
                     ''):
            with self.subTest(body=body):
                self.assertFalse(looks_like_kato_review_comment_reply(
                    ReviewComment(body=body)))


class ProviderAuthorIdTests(unittest.TestCase):
    """Each provider must populate the stable handle, or its host loops."""

    def test_review_comment_defaults_author_id_to_empty(self):
        self.assertEqual(ReviewComment().author_id, '')

    def test_author_id_survives_the_shared_builder(self):
        from provider_client_base.provider_client_base.pull_request_client_base import (
            PullRequestClientBase,
        )
        comment = PullRequestClientBase._review_comment_from_values(
            pull_request_id='7', comment_id='1',
            author='Shay Tessler', author_id='shay.te', body='x',
        )
        self.assertEqual(comment.author, 'Shay Tessler')
        self.assertEqual(comment.author_id, 'shay.te')



class TicketCommentAuthorIdentityTests(unittest.TestCase):
    """A dedicated kato account answers "did I write this?" outright.

    With a SHARED account (kato posting as the operator) the author can't
    separate the two, so wording stays the fallback. That is exactly the
    fragility a dedicated account removes.
    """

    def test_dedicated_account_wins_regardless_of_wording(self):
        from kato_core_lib.helpers.agent_comment_classification import (
            is_agent_authored_comment,
        )
        own = {'author': 'Kato Bot', 'author_id': 'kato-bot',
               'body': 'wording that matches no prefix at all'}
        self.assertTrue(is_agent_authored_comment(own, ('kato-bot',)))

    def test_a_humans_comment_is_never_claimed(self):
        from kato_core_lib.helpers.agent_comment_classification import (
            is_agent_authored_comment,
        )
        human = {'author': 'Dana', 'author_id': 'dana',
                 'body': 'please fix the retry logic'}
        self.assertFalse(is_agent_authored_comment(human, ('kato-bot',)))

    def test_display_name_is_never_compared_against_the_handle(self):
        # The trap this exists to avoid: providers render "Jane Doe" while the
        # config holds "jane.doe". Matching must read author_id only.
        from kato_core_lib.helpers.agent_comment_classification import (
            is_agent_authored_comment,
        )
        entry = {'author': 'Kato Bot', 'author_id': '',
                 'body': 'wording that matches no prefix at all'}
        self.assertFalse(is_agent_authored_comment(entry, ('kato bot',)))

    def test_shared_account_falls_back_to_wording(self):
        from kato_core_lib.helpers.agent_comment_classification import (
            AGENT_COMPLETION_COMMENT_PREFIX, is_agent_authored_comment,
        )
        latch = {'author': 'Shay Tessler', 'author_id': 'shay',
                 'body': f'{AGENT_COMPLETION_COMMENT_PREFIX}ABC-1'}
        self.assertTrue(is_agent_authored_comment(latch, ('shay',)))
        chatter = {'author': 'Shay Tessler', 'author_id': 'shay',
                   'body': 'looks good to me'}
        # Shared account: kato must NOT claim the operator's own words.
        self.assertFalse(is_agent_authored_comment(chatter, ()))

    def test_no_identities_configured_still_classifies_by_wording(self):
        from kato_core_lib.helpers.agent_comment_classification import (
            AGENT_COMPLETION_COMMENT_PREFIX, is_agent_authored_comment,
        )
        latch = {'author': 'x', 'author_id': 'x',
                 'body': f'{AGENT_COMPLETION_COMMENT_PREFIX}ABC-1'}
        self.assertTrue(is_agent_authored_comment(latch, ()))

if __name__ == '__main__':
    unittest.main()
