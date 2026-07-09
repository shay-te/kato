"""Tests for the shared @-mention comment filter.

Pin down the rule used by every ticket platform's
``_task_comment_entries`` so kato stops acting on comments addressed
to humans other than its own bot user. Every stand-in here is a
plain string / value — no Mocks, no magic.
"""
from __future__ import annotations

import unittest

from provider_client_base.provider_client_base.helpers.mention_utils import (
    extract_all_mention_tokens,
    extract_mention_logins,
    is_addressed_elsewhere_from_mentions,
    is_comment_addressed_elsewhere,
    is_comment_addressed_elsewhere_any,
    mentions_include_identity,
)


class ExtractAllMentionTokensTests(unittest.TestCase):

    def test_empty_body_returns_empty(self) -> None:
        self.assertEqual(extract_all_mention_tokens(''), [])
        self.assertEqual(extract_all_mention_tokens(None), [])

    def test_plain_login_form(self) -> None:
        self.assertEqual(
            extract_all_mention_tokens('hey @Jane.Doe and @bob'),
            ['jane.doe', 'bob'],
        )

    def test_bitbucket_brace_account_id_form(self) -> None:
        # The Bitbucket bug: mentions come as ``@{account_id}`` which the
        # plain @login regex can't see.
        self.assertEqual(
            extract_all_mention_tokens('please @{557058:abc-123} look'),
            ['557058:abc-123'],
        )

    def test_unions_both_encodings_deduped(self) -> None:
        self.assertEqual(
            extract_all_mention_tokens('@alice and @{557058:abc} and @Alice again'),
            ['alice', '557058:abc'],
        )


class MentionsIncludeIdentityTests(unittest.TestCase):

    def test_true_when_a_mention_matches_an_identity(self) -> None:
        self.assertTrue(mentions_include_identity(
            ['jane', 'review-bot'], ['review-bot', '557058:x']))

    def test_case_insensitive_and_trimmed(self) -> None:
        self.assertTrue(mentions_include_identity([' Review_Bot '], ['review_bot']))

    def test_false_when_no_match(self) -> None:
        self.assertFalse(mentions_include_identity(['jane', 'bob'], ['review-bot']))

    def test_false_when_no_identities(self) -> None:
        # No known bot identity → can't confirm the bot is tagged.
        self.assertFalse(mentions_include_identity(['jane'], []))

    def test_false_when_no_mentions(self) -> None:
        self.assertFalse(mentions_include_identity([], ['review-bot']))


class ExtractMentionLoginsTests(unittest.TestCase):

    def test_empty_body_returns_empty(self) -> None:
        self.assertEqual(extract_mention_logins(''), [])
        self.assertEqual(extract_mention_logins(None), [])
        self.assertEqual(extract_mention_logins(0), [])

    def test_finds_single_mention_lowercased(self) -> None:
        self.assertEqual(
            extract_mention_logins('hey @Jane.Doe can you check this'),
            ['jane.doe'],
        )

    def test_finds_multiple_mentions_preserving_order(self) -> None:
        self.assertEqual(
            extract_mention_logins('@kato_bot please ping @alice and @bob-jr'),
            ['kato_bot', 'alice', 'bob-jr'],
        )

    def test_email_addresses_do_not_count_as_mentions(self) -> None:
        # ``foo@example.com`` must NOT register as ``@example`` — the
        # lookbehind on ``[\w.]`` blocks email-like contexts.
        self.assertEqual(
            extract_mention_logins('email me at foo@example.com'),
            [],
        )

    def test_mentions_adjacent_to_punctuation(self) -> None:
        # Comma / period / colon directly after the login are fine.
        self.assertEqual(
            extract_mention_logins('@alice, @bob: please look. @carol.'),
            ['alice', 'bob', 'carol'],
        )

    def test_bare_at_sign_is_not_a_mention(self) -> None:
        self.assertEqual(extract_mention_logins('cost is $5 @ each'), [])
        self.assertEqual(extract_mention_logins('@'), [])

    def test_non_string_body_is_coerced_to_string(self) -> None:
        # Defensive — extract_body callbacks may return non-strings.
        self.assertEqual(
            extract_mention_logins(['@alice']),  # type: ignore[arg-type]
            ['alice'],
        )

    def test_underscore_dot_hyphen_in_login(self) -> None:
        self.assertEqual(
            extract_mention_logins('@my_user.name-v2 hello'),
            ['my_user.name-v2'],
        )


class IsCommentAddressedElsewhereTests(unittest.TestCase):

    # ---- filter disabled paths ----

    def test_empty_bot_login_disables_filter(self) -> None:
        self.assertFalse(is_comment_addressed_elsewhere('@alice please', ''))
        self.assertFalse(is_comment_addressed_elsewhere('@alice please', None))

    def test_me_alias_disables_filter(self) -> None:
        # YouTrack's ``"me"`` is a query alias, not a real login — it
        # could never literally appear in a ``@mention``. Treat as
        # "filter disabled" rather than silently keeping nothing.
        self.assertFalse(is_comment_addressed_elsewhere('@alice please', 'me'))
        self.assertFalse(is_comment_addressed_elsewhere('@alice please', 'ME'))
        self.assertFalse(is_comment_addressed_elsewhere('@alice please', '  me  '))

    # ---- the actual rule ----

    def test_no_mentions_in_body_is_kept(self) -> None:
        # General project note → kato should still see it.
        self.assertFalse(
            is_comment_addressed_elsewhere('this also needs a unit test', 'kato_bot'),
        )

    def test_mention_matches_bot_is_kept(self) -> None:
        self.assertFalse(
            is_comment_addressed_elsewhere('@kato_bot fix the typo', 'kato_bot'),
        )

    def test_mention_to_someone_else_is_skipped(self) -> None:
        # The actual reported bug.
        self.assertTrue(
            is_comment_addressed_elsewhere('@jane.doe please look at this', 'kato_bot'),
        )

    def test_bot_among_others_is_kept(self) -> None:
        # If the operator addressed kato AND someone else, the
        # comment is still meant for kato → keep it.
        self.assertTrue(
            # only @alice — not kato.
            is_comment_addressed_elsewhere('@alice and @bob', 'kato_bot'),
        )
        self.assertFalse(
            # kato is one of the addressees.
            is_comment_addressed_elsewhere('@alice and @kato_bot', 'kato_bot'),
        )

    def test_case_insensitive_match(self) -> None:
        self.assertFalse(
            is_comment_addressed_elsewhere('@Kato_Bot fix it', 'kato_bot'),
        )
        self.assertFalse(
            is_comment_addressed_elsewhere('@kato_bot fix it', 'KATO_BOT'),
        )

    def test_email_addresses_do_not_trigger_skip(self) -> None:
        # ``foo@example.com`` must not register as a mention of
        # ``example``; otherwise plain operator notes that include an
        # email would be silently dropped.
        self.assertFalse(
            is_comment_addressed_elsewhere(
                'forward this to ops@example.com please',
                'kato_bot',
            ),
        )

    def test_non_string_body_is_handled(self) -> None:
        # extract_body callbacks may return non-strings (Jira ADF,
        # numbers, lists). Filter must not crash.
        self.assertFalse(is_comment_addressed_elsewhere(42, 'kato_bot'))
        self.assertFalse(is_comment_addressed_elsewhere(None, 'kato_bot'))

    def test_bot_login_stripped_of_whitespace(self) -> None:
        self.assertTrue(
            is_comment_addressed_elsewhere('@jane please', '  kato_bot  '),
        )


class IsCommentAddressedElsewhereAnyTests(unittest.TestCase):
    """A bot known under SEVERAL logins at once (e.g. a YouTrack assignee AND
    a different Bitbucket username on a mixed deployment). A comment mentioning
    ANY of them is for the bot; one mentioning only other people is skipped.
    """

    BOT = ('kato_yt', 'kato_bb')  # task-platform login + code-host login

    # ---- filter disabled paths ----

    def test_no_logins_disables_filter(self) -> None:
        self.assertFalse(is_comment_addressed_elsewhere_any('@alice please', ()))
        self.assertFalse(is_comment_addressed_elsewhere_any('@alice please', []))

    def test_only_empty_or_me_logins_disables_filter(self) -> None:
        # Every candidate normalizes away → no usable login → keep everything,
        # exactly like the single-login form.
        self.assertFalse(
            is_comment_addressed_elsewhere_any('@alice please', ['', None, 'me', '  ME ']),
        )

    def test_none_argument_disables_filter(self) -> None:
        self.assertFalse(is_comment_addressed_elsewhere_any('@alice please', None))

    # ---- the multi-login rule ----

    def test_mention_to_someone_else_is_skipped(self) -> None:
        # Neither bot login is mentioned → addressed elsewhere → skip.
        self.assertTrue(
            is_comment_addressed_elsewhere_any('@jane.doe please look', self.BOT),
        )

    def test_mention_of_task_platform_login_is_kept(self) -> None:
        self.assertFalse(
            is_comment_addressed_elsewhere_any('@kato_yt fix the typo', self.BOT),
        )

    def test_mention_of_code_host_login_is_kept(self) -> None:
        # The crux for a mixed deployment: the reviewer @-mentions the bot
        # under its OTHER (Bitbucket) login — must NOT be dropped.
        self.assertFalse(
            is_comment_addressed_elsewhere_any('@kato_bb can you also fix X', self.BOT),
        )

    def test_bot_among_others_is_kept(self) -> None:
        self.assertFalse(
            is_comment_addressed_elsewhere_any('@alice and @kato_bb', self.BOT),
        )
        self.assertTrue(
            is_comment_addressed_elsewhere_any('@alice and @bob', self.BOT),
        )

    def test_no_mentions_in_body_is_kept(self) -> None:
        self.assertFalse(
            is_comment_addressed_elsewhere_any('this also needs a unit test', self.BOT),
        )

    def test_empty_logins_mixed_with_a_real_one_still_filter(self) -> None:
        # Blank/'me' entries are dropped but the one real login still applies.
        self.assertTrue(
            is_comment_addressed_elsewhere_any('@jane please', ['', 'me', 'kato_bb']),
        )
        self.assertFalse(
            is_comment_addressed_elsewhere_any('@kato_bb please', ['', 'me', 'kato_bb']),
        )

    def test_case_insensitive_across_logins(self) -> None:
        self.assertFalse(
            is_comment_addressed_elsewhere_any('@Kato_BB fix it', self.BOT),
        )

    def test_bare_string_is_treated_as_single_login(self) -> None:
        # Convenience: a single login may be passed as a plain string.
        self.assertTrue(is_comment_addressed_elsewhere_any('@jane please', 'kato_bb'))
        self.assertFalse(is_comment_addressed_elsewhere_any('@kato_bb please', 'kato_bb'))

    def test_non_string_body_is_handled(self) -> None:
        self.assertFalse(is_comment_addressed_elsewhere_any(42, self.BOT))
        self.assertFalse(is_comment_addressed_elsewhere_any(None, self.BOT))


class MentionMidSentenceTests(unittest.TestCase):
    """A mention embedded in prose (not at the start) must still count."""

    SENTENCE = 'he look yada yda @Alice yes ..'

    def test_extract_finds_mid_sentence_mention(self) -> None:
        self.assertEqual(extract_mention_logins(self.SENTENCE), ['alice'])

    def test_addressed_elsewhere_when_bot_is_someone_else(self) -> None:
        self.assertTrue(is_comment_addressed_elsewhere(self.SENTENCE, 'kato_bot'))

    def test_kept_when_bot_is_the_one_mentioned(self) -> None:
        self.assertFalse(is_comment_addressed_elsewhere(self.SENTENCE, 'alice'))


class IsAddressedElsewhereFromMentionsTests(unittest.TestCase):
    """Direct contract for the already-extracted-identities entry point."""

    def test_human_only_mentions_are_elsewhere(self) -> None:
        self.assertTrue(
            is_addressed_elsewhere_from_mentions(['alice'], 'kato_bot'))

    def test_bot_among_mentions_is_kept(self) -> None:
        self.assertFalse(
            is_addressed_elsewhere_from_mentions(['alice', 'kato_bot'], 'kato_bot'))

    def test_no_mentions_is_kept(self) -> None:
        self.assertFalse(is_addressed_elsewhere_from_mentions([], 'kato_bot'))
        self.assertFalse(is_addressed_elsewhere_from_mentions(None, 'kato_bot'))

    def test_empty_or_me_bot_disables(self) -> None:
        self.assertFalse(is_addressed_elsewhere_from_mentions(['alice'], ''))
        self.assertFalse(is_addressed_elsewhere_from_mentions(['alice'], 'me'))

    def test_normalizes_case_and_whitespace_on_both_sides(self) -> None:
        # Account ids / handles compared case-insensitively, stripped.
        self.assertFalse(
            is_addressed_elsewhere_from_mentions(['  Kato_Bot '], ' kato_bot '))
        self.assertTrue(
            is_addressed_elsewhere_from_mentions(['Alice'], 'Kato_Bot'))

    def test_accepts_multiple_bot_logins(self) -> None:
        # A bot known under several ids — match on ANY of them is "for bot".
        self.assertFalse(
            is_addressed_elsewhere_from_mentions(
                ['557058:abc'], ['kato_handle', '557058:abc']))
        self.assertTrue(
            is_addressed_elsewhere_from_mentions(
                ['alice-id'], ['kato_handle', '557058:abc']))

    def test_blank_only_mentions_are_kept(self) -> None:
        self.assertFalse(
            is_addressed_elsewhere_from_mentions(['', '   '], 'kato_bot'))


if __name__ == '__main__':
    unittest.main()
