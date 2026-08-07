"""The PR review-comment scan must skip comments @-mentioning a teammate.

Operator's hard rule: a review comment that @-tags ANYONE is that person's to
answer, NOT kato's — kato acts ONLY when the tag is kato itself, or when the
comment tags no one. Two encodings are caught: plain ``@login``
(GitHub/GitLab/YouTrack) and Bitbucket's ``@{account_id}``.

Crucially — and this is the change from the old behavior — when a comment tags
people but kato CANNOT confirm it is one of them (its code-host identity isn't
resolvable), the comment is SKIPPED, not processed. The operator would rather
kato stay out of a human-directed comment than jump on it; a comment genuinely
directed at kato under an unresolvable handle being left for a human is the
accepted trade-off. kato's identities are gathered from every source (code-host
login + task ``assignee``) so a real kato mention is still recognised whenever
possible.

Single-comment cases on purpose: a lone non-kato reviewer comment can only be
dropped by the mention filter (no thread-dedup partner, no position gate, not
yet processed), so a dropped/kept result is unambiguously the filter's doing.
"""
import unittest
from unittest.mock import MagicMock

from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from kato_core_lib.data_layers.service.review_comment_service import ReviewCommentService
from tests.review_mention_policy_support import legacy_mention_policy


# This module predates KATO_REVIEW_COMMENTS_REQUIRE_MENTION and asserts the
# fix pipeline / dedup semantics with plain reviewer comments. Pin the legacy
# mention rule so those assertions keep testing what they were written for;
# the new default is covered by test_review_comment_require_mention.
_MENTION_POLICY = None


def setUpModule():
    global _MENTION_POLICY
    _MENTION_POLICY = legacy_mention_policy()
    _MENTION_POLICY.start()


def tearDownModule():
    if _MENTION_POLICY is not None:
        _MENTION_POLICY.stop()


def _comment(comment_id: str, body: str) -> ReviewComment:
    return ReviewComment(
        pull_request_id='17', comment_id=comment_id, author='reviewer', body=body,
    )


class ReviewCommentMentionSkipTests(unittest.TestCase):
    def _service(self, *, task_login='kato_yt', review_login='kato_bb'):
        task_service = MagicMock()
        task_service.bot_login = task_login
        repository_service = MagicMock()
        repository_service.review_comment_bot_login.return_value = review_login
        state_registry = MagicMock()
        state_registry.is_review_comment_processed.return_value = False
        return ReviewCommentService(
            task_service=task_service,
            implementation_service=MagicMock(),
            repository_service=repository_service,
            state_registry=state_registry,
        )

    def _kept_ids(self, service, comments):
        kept = service._unprocessed_review_comments(
            comments, repository_id='client', pull_request_id='17', comment_context=[],
        )
        return [c.comment_id for c in kept]

    def test_skips_comment_mentioning_other_human(self) -> None:
        # The actual reported bug.
        service = self._service()
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@jane.doe please look at this')]),
            [],
        )

    def test_keeps_plain_comment_with_no_mention(self) -> None:
        service = self._service()
        self.assertEqual(
            self._kept_ids(service, [_comment('1', 'this also needs a unit test')]),
            ['1'],
        )

    def test_keeps_comment_mentioning_bot_code_host_login(self) -> None:
        service = self._service()
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@kato_bb also handle X')]),
            ['1'],
        )

    def test_keeps_comment_mentioning_bot_task_login_as_secondary(self) -> None:
        # When the code-host login IS known, the task assignee is a secondary
        # identity, so a comment @-mentioning the bot under it is kept too.
        service = self._service()
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@kato_yt fix the typo')]),
            ['1'],
        )

    def test_keeps_comment_mentioning_bot_among_others(self) -> None:
        service = self._service()
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@jane and @kato_bb please')]),
            ['1'],
        )

    def test_unknown_code_host_identity_still_skips_a_human_tag(self) -> None:
        # Operator rule (the change): the bot's review-platform login can't be
        # resolved, but the comment clearly tags a human — kato stays out of
        # it rather than processing a comment meant for jane. (Old behavior
        # kept it; that was the reported bug.)
        service = self._service(task_login='kato_yt', review_login='')
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@jane.doe please look')]),
            [],
        )

    def test_no_identities_at_all_still_skips_a_human_tag(self) -> None:
        # No resolvable kato identity at all → a tagged comment is definitely
        # not confirmably kato's → skip.
        service = self._service(task_login='', review_login='')
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@jane.doe please look')]),
            [],
        )

    def test_bitbucket_brace_account_id_mention_is_seen(self) -> None:
        # Bitbucket encodes mentions as ``@{account_id}`` — the plain @login
        # regex couldn't see it, so tagged Bitbucket comments slipped through.
        # Now the brace form is caught: a comment tagging a human account id
        # is skipped, one tagging the bot's account id is kept.
        service = self._service(task_login='kato_yt', review_login='557058:kato-bot')
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@{557058:teammate} look here')]),
            [],
        )
        self.assertEqual(
            self._kept_ids(service, [_comment('2', '@{557058:kato-bot} handle X')]),
            ['2'],
        )

    def test_me_secondary_login_is_normalized_away(self) -> None:
        # Code-host login known; task assignee is the YouTrack 'me' alias which
        # is treated as "no login" — filter still works off the code-host login.
        service = self._service(task_login='me', review_login='kato_bb')
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@jane.doe please look')]),
            [],
        )
        self.assertEqual(
            self._kept_ids(service, [_comment('2', '@kato_bb please look')]),
            ['2'],
        )

    def test_identity_lookup_failure_does_not_break_selection(self) -> None:
        # A best-effort identity lookup that raises must not crash the scan.
        # The code-host identity is lost, but the task assignee (kato_yt) is
        # still known — and @jane.doe isn't it, so the human-tagged comment is
        # still correctly skipped.
        service = self._service(task_login='kato_yt')
        service._repository_service.review_comment_bot_login.side_effect = RuntimeError('boom')
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@jane.doe please look')]),
            [],
        )

    def test_code_host_login_alone_when_no_task_assignee(self) -> None:
        # review_login known, task assignee unset → filter runs on the
        # code-host login alone (no secondary identity appended).
        service = self._service(task_login='', review_login='kato_bb')
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@jane please')]), [],
        )
        self.assertEqual(
            self._kept_ids(service, [_comment('2', '@kato_bb please')]), ['2'],
        )

    def test_task_login_lookup_failure_falls_back_to_code_host(self) -> None:
        # A task_service.bot_login property that raises must not crash; the
        # filter proceeds on the resolved code-host identity alone.
        class _RaisingBotLogin:
            @property
            def bot_login(self):
                raise RuntimeError('boom')

        repository_service = MagicMock()
        repository_service.review_comment_bot_login.return_value = 'kato_bb'
        state_registry = MagicMock()
        state_registry.is_review_comment_processed.return_value = False
        service = ReviewCommentService(
            task_service=_RaisingBotLogin(),
            implementation_service=MagicMock(),
            repository_service=repository_service,
            state_registry=state_registry,
        )
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@jane please')]), [],
        )
        self.assertEqual(
            self._kept_ids(service, [_comment('2', '@kato_bb please')]), ['2'],
        )


if __name__ == '__main__':
    unittest.main()
