"""Kato must never read its OWN review replies as reviewer instructions.

Two independent defects made kato do exactly that, and together they are a
mechanical explanation for the operator report "kato isn't connected to what
happened before / comes to the comment like a noob" on review comments.

DEFECT 1 — the streaming review-fix path built its prompts without
``self_reply_prefixes``. That kwarg is what drops kato's own
"Kato addressed review comment …" replies out of the thread context; without
it they were rendered back into the prompt's "Review comment context" block as
though a reviewer had written them, so the agent re-read its own past output as
fresh instructions. The one-shot client path passed the kwarg; the STREAMING
path — the one the planning UI actually uses — did not.

DEFECT 2 — ``_review_bot_identities`` hand-rolled its identity normalization
and knew only about YouTrack's ``me`` alias, so a Jira ``assignee:
currentUser()`` setup produced the identity tuple ``('currentuser()',)``. That
is strictly worse than an EMPTY tuple: ``is_kato_review_comment_reply`` treats
an empty identity set as "trust the reply-prefix alone" and returns True, but a
non-empty set makes it compare kato's real PR author name against the junk —
which can never match — so kato stopped recognising its own replies and
re-processed them as unaddressed reviewer comments.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
from kato_core_lib.data_layers.service.review_comment_service import (
    ReviewCommentService,
)
from kato_core_lib.helpers.review_comment_utils import (
    KATO_REVIEW_COMMENT_ANSWER_PREFIX,
    KATO_REVIEW_COMMENT_FIXED_PREFIX,
    KATO_REVIEW_COMMENT_NO_CHANGES_PREFIX,
    KATO_REVIEW_COMMENT_REPLY_PREFIX,
    KATO_SELF_REPLY_PREFIXES,
    is_kato_review_comment_reply,
)


def _comment_with_kato_reply(reply_body: str) -> SimpleNamespace:
    """A review thread whose second entry is kato's own posted reply."""
    return SimpleNamespace(
        pull_request_id='1', comment_id='c1', author='reviewer',
        body='please fix the guard', file_path='', line_number='',
        line_type='', commit_sha='', repository_id='r',
        all_comments=[
            {'author': 'reviewer', 'body': 'please fix the guard'},
            {'author': 'kato', 'body': reply_body},
        ],
    )


class SelfReplyPrefixSetTests(unittest.TestCase):
    """The canonical set must cover EVERY prefix kato posts.

    Missing one leaks that reply category back into future prompts.
    """

    def test_covers_all_four_reply_categories(self) -> None:
        self.assertEqual(
            set(KATO_SELF_REPLY_PREFIXES),
            {
                KATO_REVIEW_COMMENT_FIXED_PREFIX,
                KATO_REVIEW_COMMENT_REPLY_PREFIX,
                KATO_REVIEW_COMMENT_ANSWER_PREFIX,
                KATO_REVIEW_COMMENT_NO_CHANGES_PREFIX,
            },
        )

    def test_every_prefix_is_recognised_as_katos_own_reply(self) -> None:
        # The prompt filter and the re-poll guard must agree on the set, or
        # kato either re-reads or re-processes its own replies.
        for prefix in KATO_SELF_REPLY_PREFIXES:
            comment = SimpleNamespace(author='kato-bot', body=f'{prefix} extra text')
            self.assertTrue(
                is_kato_review_comment_reply(comment, ('kato-bot',)),
                f'unrecognised self-reply prefix: {prefix!r}',
            )


class StreamingReviewPromptHygieneTests(unittest.TestCase):
    """DEFECT 1: the streaming path must filter kato's own replies."""

    def test_prefixes_drop_katos_reply_from_the_prompt(self) -> None:
        comment = _comment_with_kato_reply(
            f'{KATO_REVIEW_COMMENT_FIXED_PREFIX}c1 on branch feature/x',
        )
        leaked = ClaudeCliClient._build_review_prompt(comment, 'feature/x')
        filtered = ClaudeCliClient._build_review_prompt(
            comment, 'feature/x', self_reply_prefixes=KATO_SELF_REPLY_PREFIXES,
        )
        # Without the kwarg the reply leaks (this is what the streaming path did)...
        self.assertIn(KATO_REVIEW_COMMENT_FIXED_PREFIX, leaked)
        # ...with it, it does not.
        self.assertNotIn(KATO_REVIEW_COMMENT_FIXED_PREFIX, filtered)
        # The reviewer's actual comment survives either way.
        self.assertIn('please fix the guard', filtered)

    def test_streaming_runner_passes_both_product_params(self) -> None:
        # Structural: the runner must hand the builders the same product
        # params the one-shot client path does. Asserted on the runner's
        # SOURCE because the prompt is built inline before any injection
        # seam exists, and a missing kwarg is silent at runtime.
        import inspect

        from kato_core_lib.data_layers.service import planning_session_runner

        source = inspect.getsource(
            planning_session_runner.PlanningSessionRunner.fix_review_comments,
        )
        self.assertIn('self_reply_prefixes', source)
        self.assertIn('workspace_refusal_guidance', source)

    def test_answer_mode_also_filters_self_replies(self) -> None:
        # Answer-mode replies carry their own prefix and must not be fed back
        # either — a question re-answered from its own prior answer is the
        # same defect wearing a different prefix.
        comment = _comment_with_kato_reply(
            f'{KATO_REVIEW_COMMENT_ANSWER_PREFIX} the guard runs first.',
        )
        filtered = ClaudeCliClient._build_review_prompt(
            comment, 'feature/x', mode='answer',
            self_reply_prefixes=KATO_SELF_REPLY_PREFIXES,
        )
        self.assertNotIn(KATO_REVIEW_COMMENT_ANSWER_PREFIX, filtered)


class BotIdentityNormalizationTests(unittest.TestCase):
    """DEFECT 2: a query alias must never become a bot "identity"."""

    def _service(self, *, review_login: str, task_login: str) -> ReviewCommentService:
        repository_service = MagicMock()
        repository_service.review_comment_bot_login.return_value = review_login
        return ReviewCommentService(
            task_service=SimpleNamespace(bot_login=task_login),
            implementation_service=MagicMock(),
            repository_service=repository_service,
            state_registry=MagicMock(),
        )

    def test_jira_currentuser_alias_is_dropped(self) -> None:
        service = self._service(review_login='', task_login='currentUser()')
        self.assertEqual(service._review_bot_identities('r1'), ())

    def test_youtrack_me_alias_is_dropped(self) -> None:
        service = self._service(review_login='', task_login='me')
        self.assertEqual(service._review_bot_identities('r1'), ())

    def test_real_identities_survive_and_are_deduped(self) -> None:
        service = self._service(review_login='Kato-Bot', task_login='kato-bot')
        self.assertEqual(service._review_bot_identities('r1'), ('kato-bot',))

    def test_alias_is_dropped_but_a_real_login_beside_it_is_kept(self) -> None:
        service = self._service(review_login='kato-bb', task_login='currentUser()')
        self.assertEqual(service._review_bot_identities('r1'), ('kato-bb',))

    def test_a_junk_only_identity_no_longer_blinds_the_reply_guard(self) -> None:
        # THE regression. With ('currentuser()',) as the identity set,
        # is_kato_review_comment_reply compared kato's real author against the
        # alias, never matched, and returned False — so kato re-processed its
        # own reply forever. Dropping the alias restores the safe empty-set
        # path, where a matching reply prefix alone is enough.
        own_reply = SimpleNamespace(
            author='kato-bb',
            body=f'{KATO_REVIEW_COMMENT_FIXED_PREFIX}c1 on branch feature/x',
        )
        self.assertFalse(
            is_kato_review_comment_reply(own_reply, ('currentuser()',)),
            'pre-fix behaviour: one junk identity blinds the guard',
        )
        service = self._service(review_login='', task_login='currentUser()')
        self.assertTrue(
            is_kato_review_comment_reply(
                own_reply, service._review_bot_identities('r1'),
            ),
            'kato must recognise its own reply once the alias is normalized away',
        )

    def test_a_genuine_third_party_reply_is_still_not_katos(self) -> None:
        # The fix must not make the guard indiscriminate: a human reply that
        # does NOT carry a kato prefix is never kato's own.
        human = SimpleNamespace(author='dave', body='I disagree, revert it')
        self.assertFalse(is_kato_review_comment_reply(human, ('kato-bb',)))


if __name__ == '__main__':
    unittest.main()
