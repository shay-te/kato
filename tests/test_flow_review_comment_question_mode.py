"""Flow #6 — Bitbucket / GitHub review comment is a question (answer-only).

A-Z scenario:

    1. PR is in "In Review" state with kato as author.
    2. Reviewer posts a question-shaped comment: ends with ``?``, starts
       with a question word, no fix keywords, ≤400 chars.
    3. Scan picks it up, batches by PR, asks ``is_question_only_batch``.
    4. Question-only batch → route to ANSWER mode.
    5. Agent reads the code and writes a plain-text answer.
    6. kato posts the answer as a reply to the comment, prefixed with
       the visible "NO CODE CHANGED" disclaimer.
    7. The thread is NOT resolved — it stays open so the reviewer can
       re-phrase as an imperative if a code change was actually wanted.

Why this matters: before answer-mode, kato treated every comment as
a fix request, pushing a "follow-up update" comment even when nothing
changed. Reviewers couldn't tell which comments had been addressed
and which had been silently answered. The "NO CODE CHANGED"
disclaimer is the load-bearing signal.

Adversarial cases this test pins:
    - "Should this be a constant?" — has ``?`` but reads as a fix
      request (``should be`` is a fix word). MUST route to fix.
    - "Why doesn't this work" — no ``?`` at the end. MUST route to fix.
    - "Add a unit test?" — starts with imperative ``add``. MUST route to fix.
    - 500-char essay ending in ``?`` — over the length cap. MUST route to fix.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from kato_core_lib.helpers.review_comment_utils import (
    ReviewReplyTemplate,
    is_question_comment,
    is_question_only_batch,
    review_comment_answer_body,
)


def _comment(body, comment_id='c1', pull_request_id='pr1'):
    return SimpleNamespace(
        body=body, comment_id=comment_id, pull_request_id=pull_request_id,
    )


# ---------------------------------------------------------------------------
# Classification — the gate between answer-mode and fix-mode.
# ---------------------------------------------------------------------------


class FlowReviewCommentQuestionClassificationTests(unittest.TestCase):

    def test_flow_question_canonical_form_routes_to_answer_mode(self) -> None:
        # Textbook question: ``how`` start + ``?`` end, short, no fix words.
        self.assertTrue(is_question_comment(_comment(
            'How does the cache invalidation work here?',
        )))

    def test_flow_question_with_all_supported_start_words(self) -> None:
        # Lock the whitelist of starter words. Some words (``Should``,
        # ``Are``, etc) also have fix-keyword overlaps — e.g. ``Should
        # this`` is a fix request, not a question — so we phrase each
        # test sentence to avoid those overlaps. The starter alone
        # must classify the comment as a question when no fix words
        # are present.
        for start, suffix in (
            ('How', 'does the cache invalidate'),
            ('Why', 'is the lock per-task'),
            ('What', 'happens on conflict'),
            ('When', 'does retry kick in'),
            ('Where', 'is the timeout configured'),
            ('Who', 'owns the lifecycle here'),
            ('Which', 'lock applies on shutdown'),
            ('Could', 'you clarify the flush order'),
            ('Can', 'you describe the eviction policy'),
            ('Will', 'this affect existing rows'),
            ('Would', 'a backoff help here'),
            ('Is', 'the cache cleared on shutdown'),
            ('Do', 'callers expect ordering guarantees'),
            ('Does', 'the writer wait on flush'),
            ('Did', 'the previous version use locks'),
            ('Have', 'you considered batch writes'),
            ('Has', 'this approach been benchmarked'),
        ):
            with self.subTest(start=start):
                self.assertTrue(
                    is_question_comment(_comment(f'{start} {suffix}?')),
                    f'{start!r}-prefixed question lost answer-mode',
                )

    def test_flow_question_without_question_mark_routes_to_fix(self) -> None:
        # No ``?`` → not a question. Defaults to fix.
        self.assertFalse(is_question_comment(_comment(
            'How does the cache invalidation work here',
        )))

    def test_flow_question_without_question_start_word_routes_to_fix(self) -> None:
        # Has ``?`` but doesn't start with a question word.
        self.assertFalse(is_question_comment(_comment(
            'I think this should be cached?',
        )))

    def test_flow_question_with_fix_keyword_routes_to_fix(self) -> None:
        # Critical adversarial case: ``should be`` is a fix-request
        # marker. Even though it ends in ``?`` and starts with ``Should``,
        # the comment is a request to change the code.
        self.assertFalse(is_question_comment(_comment(
            'Should this be a constant?',
        )), 'fix-request leaked into answer-mode — kato will not fix')

    def test_flow_question_with_imperative_routes_to_fix(self) -> None:
        # ``Add`` is a fix-request word — even with ``?`` at the end
        # (``Add a unit test?``), this is a fix request from the
        # reviewer.
        self.assertFalse(is_question_comment(_comment(
            'Add a unit test?',
        )))

    def test_flow_question_too_long_routes_to_fix(self) -> None:
        # Length cap (400 chars). Long comments usually bury a fix
        # request inside the explanation; the conservative default
        # is fix.
        long_q = 'How ' + ('x' * 500) + '?'
        self.assertFalse(is_question_comment(_comment(long_q)))

    def test_flow_question_at_exactly_max_length_routes_to_answer(self) -> None:
        # Boundary: exactly 400 chars passes. (Off-by-one regression
        # would silently downgrade boundary-length questions to fix.)
        body = 'How ' + ('x' * (400 - len('How ') - 1)) + '?'
        self.assertEqual(len(body), 400)
        self.assertTrue(is_question_comment(_comment(body)))

    def test_flow_question_empty_body_routes_to_fix(self) -> None:
        self.assertFalse(is_question_comment(_comment('')))

    def test_flow_question_whitespace_body_routes_to_fix(self) -> None:
        self.assertFalse(is_question_comment(_comment('   \n\t  ')))

    def test_flow_question_only_a_question_mark_routes_to_fix(self) -> None:
        # Edge: ``?`` alone. Ends in ``?`` but has no start word.
        self.assertFalse(is_question_comment(_comment('?')))

    def test_flow_question_lower_case_question_word_still_classified(self) -> None:
        # Reviewers don't always capitalize. Classification is
        # case-insensitive.
        self.assertTrue(is_question_comment(_comment('how is this safe?')))


# ---------------------------------------------------------------------------
# Batch classification: any non-question downgrades the entire batch.
# ---------------------------------------------------------------------------


class FlowReviewCommentBatchClassificationTests(unittest.TestCase):

    def test_flow_question_batch_all_questions_routes_to_answer(self) -> None:
        comments = [
            _comment('How does the cache invalidate?'),
            _comment('Why is the lock per-task rather than global?'),
        ]
        self.assertTrue(is_question_only_batch(comments))

    def test_flow_question_batch_mixed_with_one_fix_routes_to_fix(self) -> None:
        # If any comment looks like a fix, the whole batch goes to fix.
        # Why: splitting a batch into two agent spawns erases batching
        # efficiency, and the reviewer's intent is mixed.
        comments = [
            _comment('How does the cache invalidate?'),
            _comment('Add a null check please.'),
        ]
        self.assertFalse(is_question_only_batch(comments))

    def test_flow_question_batch_empty_returns_false(self) -> None:
        # Empty batch can't be "all questions" — answer-mode requires
        # at least one question to actually answer.
        self.assertFalse(is_question_only_batch([]))

    def test_flow_question_batch_none_returns_false(self) -> None:
        self.assertFalse(is_question_only_batch(None))


# ---------------------------------------------------------------------------
# Reply-body construction: the "NO CODE CHANGED" disclaimer is mandatory.
# ---------------------------------------------------------------------------


class FlowReviewCommentAnswerBodyTests(unittest.TestCase):

    def test_flow_question_reply_body_starts_with_no_code_changed_disclaimer(self) -> None:
        # The load-bearing operator-trust signal. If THIS fails, the
        # reviewer cannot tell answer replies from fix replies and
        # will assume code was pushed when it wasn't.
        execution = {'message': 'The cache uses last-write-wins.'}
        body = review_comment_answer_body(execution)
        self.assertIn('No code was changed', body)
        self.assertIn('nothing was pushed', body)

    def test_flow_question_reply_body_uses_canonical_template(self) -> None:
        # Stronger lock: the answer header is the literal template
        # string. Bitbucket strips `<small>` so the template uses
        # `<sub>`; a regression to `<small>` would render unformatted.
        execution = {'message': 'Yes, this is intentional.'}
        body = review_comment_answer_body(execution)
        self.assertTrue(body.startswith(ReviewReplyTemplate.ANSWER_HEADER))

    def test_flow_question_reply_body_separates_header_from_answer(self) -> None:
        # The separator (`---`) makes header vs content visually
        # distinct. Without it the reviewer sees one blob of text.
        execution = {'message': 'The cache uses last-write-wins.'}
        body = review_comment_answer_body(execution)
        self.assertIn(ReviewReplyTemplate.SEPARATOR.strip(), body)

    def test_flow_question_reply_body_includes_agent_answer_verbatim(self) -> None:
        agent_text = 'Cache is invalidated on write via a TTL of 60s.'
        body = review_comment_answer_body({'message': agent_text})
        self.assertIn(agent_text, body)

    def test_flow_question_reply_body_prefers_message_field(self) -> None:
        # Backends populate different keys. The builder must pick a
        # consistent priority order; if it ever flips, regressions
        # surface as missing answers.
        body = review_comment_answer_body({
            'message': 'PRIMARY',
            'result': 'FALLBACK-1',
            'summary': 'FALLBACK-2',
        })
        self.assertIn('PRIMARY', body)
        self.assertNotIn('FALLBACK-1', body)

    def test_flow_question_reply_body_falls_back_to_result(self) -> None:
        body = review_comment_answer_body({
            'result': 'FROM-RESULT-FIELD',
        })
        self.assertIn('FROM-RESULT-FIELD', body)

    def test_flow_question_reply_body_falls_back_to_summary(self) -> None:
        body = review_comment_answer_body({
            'summary': 'FROM-SUMMARY-FIELD',
        })
        self.assertIn('FROM-SUMMARY-FIELD', body)

    def test_flow_question_reply_body_with_no_fields_has_explicit_fallback(self) -> None:
        # Worst-case: agent produced nothing usable. The body must
        # still ship with a non-empty answer placeholder + the
        # disclaimer, NOT an empty body.
        body = review_comment_answer_body({})
        self.assertIn('No code was changed', body)
        self.assertGreater(
            len(body), len(ReviewReplyTemplate.ANSWER_HEADER) + 10,
            'empty-execution answer body is just the header — reviewer '
            'sees a confused boilerplate reply',
        )

    def test_flow_question_reply_body_handles_none_field_values(self) -> None:
        # Adversarial: backend put ``None`` instead of a string.
        # Builder must coerce safely, not crash.
        body = review_comment_answer_body({
            'message': None,
            'result': 'fallback ok',
        })
        self.assertIn('fallback ok', body)

    def test_flow_question_reply_body_truthy_imperative_does_not_override_disclaimer(self) -> None:
        # LLM hallucination: agent answer text claims "I pushed a fix."
        # The disclaimer MUST still appear above the answer so the
        # reviewer trusts the header over the hallucinated body.
        body = review_comment_answer_body({
            'message': 'I pushed a fix in commit abc1234.',
        })
        # Disclaimer comes first.
        self.assertLess(
            body.index('No code was changed'),
            body.index('I pushed a fix'),
            'hallucinated "I pushed" landed before the disclaimer',
        )


if __name__ == '__main__':
    unittest.main()
