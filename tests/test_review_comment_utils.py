import unittest

from kato_core_lib.data_layers.data.fields import (
    PullRequestFields,
    ReviewCommentFields,
)
from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from kato_core_lib.helpers.review_comment_utils import (
    KATO_REVIEW_COMMENT_NO_CHANGES_PREFIX,
    ReviewReplyTemplate,
    is_kato_review_comment_reply,
    normalize_comment_context,
    review_comment_from_payload,
)


class ReviewCommentUtilsTests(unittest.TestCase):
    def test_review_comment_from_payload_builds_entity(self) -> None:
        comment = review_comment_from_payload(
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                ReviewCommentFields.PULL_REQUEST_ID: '17',
                ReviewCommentFields.COMMENT_ID: '99',
                ReviewCommentFields.AUTHOR: 'reviewer',
                ReviewCommentFields.BODY: 'Please rename this variable.',
            }
        )

        self.assertEqual(comment.pull_request_id, '17')
        self.assertEqual(comment.comment_id, '99')
        self.assertEqual(comment.author, 'reviewer')
        self.assertEqual(comment.body, 'Please rename this variable.')
        self.assertEqual(getattr(comment, PullRequestFields.REPOSITORY_ID), 'client')

    def test_review_comment_from_payload_raises_value_error_for_invalid_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, 'invalid review comment payload'):
            review_comment_from_payload({ReviewCommentFields.PULL_REQUEST_ID: '17'})

    def test_review_comment_from_payload_stringifies_non_string_values(self) -> None:
        comment = review_comment_from_payload(
            {
                ReviewCommentFields.PULL_REQUEST_ID: 17,
                ReviewCommentFields.COMMENT_ID: 99,
                ReviewCommentFields.AUTHOR: None,
                ReviewCommentFields.BODY: 12345,
            }
        )

        self.assertEqual(comment.pull_request_id, '17')
        self.assertEqual(comment.comment_id, '99')
        self.assertEqual(comment.author, 'None')
        self.assertEqual(comment.body, '12345')

    def test_review_comment_from_payload_normalizes_comment_context(self) -> None:
        comment = review_comment_from_payload(
            {
                ReviewCommentFields.PULL_REQUEST_ID: '17',
                ReviewCommentFields.COMMENT_ID: '99',
                ReviewCommentFields.AUTHOR: 'reviewer',
                ReviewCommentFields.BODY: 'Please rename this variable.',
                ReviewCommentFields.ALL_COMMENTS: [
                    ReviewComment(
                        pull_request_id='17',
                        comment_id='98',
                        author='reviewer',
                        body='Please add a test.',
                    )
                ],
            }
        )

        self.assertEqual(
            getattr(comment, ReviewCommentFields.ALL_COMMENTS),
            [
                {
                    ReviewCommentFields.COMMENT_ID: '98',
                    ReviewCommentFields.AUTHOR: 'reviewer',
                    ReviewCommentFields.BODY: 'Please add a test.',
                }
            ],
        )

    def test_review_comment_from_payload_handles_unicode_characters(self) -> None:
        comment = review_comment_from_payload(
            {
                ReviewCommentFields.PULL_REQUEST_ID: '17',
                ReviewCommentFields.COMMENT_ID: '99',
                ReviewCommentFields.AUTHOR: 'test-user',
                ReviewCommentFields.BODY: 'Test with unicode: café, naïve, résumé, 🚀',
            }
        )

        self.assertEqual(comment.body, 'Test with unicode: café, naïve, résumé, 🚀')

    def test_review_comment_from_payload_handles_empty_body(self) -> None:
        comment = review_comment_from_payload(
            {
                ReviewCommentFields.PULL_REQUEST_ID: '17',
                ReviewCommentFields.COMMENT_ID: '99',
                ReviewCommentFields.AUTHOR: 'test-user',
                ReviewCommentFields.BODY: '',
            }
        )

        self.assertEqual(comment.body, '')

    def test_review_comment_from_payload_handles_special_characters(self) -> None:
        comment = review_comment_from_payload(
            {
                ReviewCommentFields.PULL_REQUEST_ID: '17',
                ReviewCommentFields.COMMENT_ID: '99',
                ReviewCommentFields.AUTHOR: 'user\"with\"quotes',
                ReviewCommentFields.BODY: 'Body with \'single\' and "double" quotes',
            }
        )

        self.assertEqual(comment.author, 'user\"with\"quotes')
        self.assertEqual(comment.body, 'Body with \'single\' and "double" quotes')

    def test_normalize_comment_context_edge_cases(self) -> None:
        self.assertEqual(
            normalize_comment_context([
                {'invalid_field': 'should_be_ignored'},
                None,
                'not_a_dict_or_object',
            ]),
            [],
        )
        self.assertEqual(normalize_comment_context(None), [])

    def test_is_kato_review_comment_reply_recognizes_answer_mode(self) -> None:
        # Regression: an answer-mode reply opens with the bolded
        # "No code was changed" disclaimer, NOT the "Kato addressed…"
        # prefix. kato must still recognise it as its own reply —
        # otherwise it re-answers its own answer and loses track of
        # the operator's follow-up.
        answer = ReviewComment(
            pull_request_id='17',
            comment_id='200',
            author='kato',
            body=(
                f'{ReviewReplyTemplate.ANSWER_HEADER}\n\n'
                'The null case is handled at line 88.'
            ),
        )
        self.assertTrue(is_kato_review_comment_reply(answer))

    def test_is_kato_review_comment_reply_recognizes_no_changes_reply(self) -> None:
        # Regression: kato's "agent ran but produced no commits" reply
        # opens with KATO_REVIEW_COMMENT_NO_CHANGES_PREFIX, not the
        # "Kato addressed…" prefix. Before this prefix was registered,
        # kato never recognised its own "no changes" reply on the next
        # poll, re-processed it as an unaddressed reviewer comment, and
        # reposted "no changes" every scan tick forever.
        no_changes = ReviewComment(
            pull_request_id='17',
            comment_id='202',
            author='kato',
            body=(
                f'{KATO_REVIEW_COMMENT_NO_CHANGES_PREFIX} The comment '
                'has not been resolved — please review the agent\'s '
                'reasoning in the planning UI and either re-prompt with '
                'more context, edit the file directly, or resolve the '
                'comment yourself if no change is needed.'
            ),
        )
        self.assertTrue(is_kato_review_comment_reply(no_changes))

    def test_is_kato_review_comment_reply_false_for_plain_reviewer_comment(self) -> None:
        reviewer = ReviewComment(
            pull_request_id='17',
            comment_id='201',
            author='reviewer',
            body='No, that is wrong — use option A.',
        )
        self.assertFalse(is_kato_review_comment_reply(reviewer))

    def test_is_kato_review_comment_reply_rejects_spoofed_author_when_identity_known(self) -> None:
        # Regression: the reply-header prefix is public text — literally
        # what kato posts on every PR it has ever touched — so any PR
        # participant can copy it into their own comment to make a real
        # reviewer's unaddressed comment (e.g. a flagged vulnerability)
        # look "already handled" and get it silently, permanently
        # skipped. When the bot's own identity is known, a prefix match
        # from a DIFFERENT author must not count as kato's own reply.
        spoofed = ReviewComment(
            pull_request_id='17',
            comment_id='101',
            author='mallory',
            body='Kato addressed this review comment, nothing more to do here.',
        )
        self.assertFalse(is_kato_review_comment_reply(spoofed, ('kato-bot',)))
        self.assertTrue(is_kato_review_comment_reply(spoofed))  # identity unknown -> old fallback

    def test_is_kato_review_comment_reply_accepts_real_author_when_identity_known(self) -> None:
        real_reply = ReviewComment(
            pull_request_id='17',
            comment_id='102',
            author='kato-bot',
            body='Kato addressed this review comment and pushed a follow-up update.',
        )
        self.assertTrue(is_kato_review_comment_reply(real_reply, ('kato-bot',)))
        self.assertTrue(is_kato_review_comment_reply(real_reply, ('KATO-BOT',)))  # case-insensitive
