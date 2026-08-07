"""``KATO_REVIEW_COMMENTS_REQUIRE_MENTION`` — kato answers only PR comments
that @-mention it.

A pull request is a conversation between reviewers. Before this, a comment
tagging NOBODY was treated as kato's to act on, so kato waded into
reviewer-to-reviewer discussion. On by default: tag kato or kato stays out.
"""

import types
import unittest
from unittest import mock
from unittest.mock import Mock

from provider_client_base.provider_client_base.data.review_comment import ReviewComment

from kato_core_lib.data_layers.service.agent_state_registry import AgentStateRegistry
from kato_core_lib.data_layers.service.review_comment_service import ReviewCommentService
from kato_core_lib.helpers import review_comment_gate_utils

BOT = ('kato-bot',)


def _comment(comment_id, body, target='T1'):
    comment = ReviewComment(
        pull_request_id='7', comment_id=comment_id,
        author='Dana Reviewer', author_id='dana', body=body,
    )
    setattr(comment, 'resolution_target_id', target)
    return comment


def _service():
    return ReviewCommentService(
        task_service=types.SimpleNamespace(bot_login='kato-bot'),
        implementation_service=types.SimpleNamespace(),
        repository_service=Mock(
            review_comment_bot_login=Mock(return_value='kato-bot')),
        state_registry=AgentStateRegistry(),
    )


def _require(value):
    """Pin the switch without touching the operator's real settings file."""
    return mock.patch.object(
        review_comment_gate_utils, 'read_kato_settings',
        return_value={'KATO_REVIEW_COMMENTS_REQUIRE_MENTION': value},
    )


class SwitchResolutionTests(unittest.TestCase):
    def test_defaults_to_on(self):
        with mock.patch.object(
            review_comment_gate_utils, 'read_kato_settings', return_value={},
        ):
            self.assertTrue(
                review_comment_gate_utils.review_comments_require_mention({}))

    def test_settings_file_beats_the_shell(self):
        with _require('false'):
            self.assertFalse(
                review_comment_gate_utils.review_comments_require_mention(
                    {'KATO_REVIEW_COMMENTS_REQUIRE_MENTION': 'true'}))

    def test_shell_used_when_settings_is_silent(self):
        with mock.patch.object(
            review_comment_gate_utils, 'read_kato_settings', return_value={},
        ):
            self.assertFalse(
                review_comment_gate_utils.review_comments_require_mention(
                    {'KATO_REVIEW_COMMENTS_REQUIRE_MENTION': 'off'}))

    def test_a_corrupt_settings_file_does_not_decide_the_switch(self):
        with mock.patch.object(
            review_comment_gate_utils, 'read_kato_settings',
            side_effect=ValueError('bad json'),
        ):
            self.assertTrue(
                review_comment_gate_utils.review_comments_require_mention({}))


class RequireMentionOnTests(unittest.TestCase):
    """The requested behaviour: only @-mentioned comments are acted on."""

    def _filter(self, body):
        with _require('true'):
            return ReviewCommentService._review_comment_not_for_kato(body, BOT)

    def test_comment_mentioning_kato_is_acted_on(self):
        self.assertFalse(self._filter('@kato-bot please fix the retry'))

    def test_mention_is_case_insensitive(self):
        self.assertFalse(self._filter('@Kato-Bot please fix the retry'))

    def test_comment_tagging_nobody_is_ignored(self):
        # The change: reviewer-to-reviewer chatter is not an instruction.
        self.assertTrue(self._filter('I think this loop allocates twice'))

    def test_comment_tagging_someone_else_is_ignored(self):
        self.assertTrue(self._filter('@dana can you take a look?'))

    def test_kato_among_several_mentions_is_acted_on(self):
        self.assertFalse(self._filter('@dana @kato-bot one for each of you'))

    def test_bitbucket_brace_mention_of_kato_counts(self):
        with _require('true'):
            self.assertFalse(
                ReviewCommentService._review_comment_not_for_kato(
                    '@{kato-bot} please fix', BOT))

    def test_fails_closed_when_the_bot_identity_is_unknown(self):
        # No identity → no mention can match → kato acts on nothing rather
        # than answering comments it can't confirm were addressed to it.
        with _require('true'):
            for body in ('@kato-bot fix it', 'plain comment'):
                self.assertTrue(
                    ReviewCommentService._review_comment_not_for_kato(body, ()))


class RequireMentionOffTests(unittest.TestCase):
    """Switching it off restores the older, looser rule."""

    def _filter(self, body):
        with _require('false'):
            return ReviewCommentService._review_comment_not_for_kato(body, BOT)

    def test_untagged_comment_is_acted_on_again(self):
        self.assertFalse(self._filter('I think this loop allocates twice'))

    def test_comment_tagging_someone_else_is_still_ignored(self):
        self.assertTrue(self._filter('@dana can you take a look?'))

    def test_comment_mentioning_kato_is_acted_on(self):
        self.assertFalse(self._filter('@kato-bot please fix the retry'))


class IntakeIntegrationTests(unittest.TestCase):
    """End to end through ``_unprocessed_review_comments``."""

    def _run(self, comments, require):
        with _require(require):
            return _service()._unprocessed_review_comments(
                comments, repository_id='client', pull_request_id='7',
                comment_context=[],
            )

    def test_only_the_mentioning_comment_survives(self):
        comments = [
            _comment('100', 'this whole file needs a rewrite', target='T1'),
            _comment('101', '@kato-bot please split this function', target='T2'),
            _comment('102', '@dana thoughts?', target='T3'),
        ]
        result = self._run(comments, 'true')
        self.assertEqual([c.comment_id for c in result], ['101'])

    def test_switching_it_off_lets_the_untagged_comment_through(self):
        comments = [
            _comment('100', 'this whole file needs a rewrite', target='T1'),
            _comment('102', '@dana thoughts?', target='T3'),
        ]
        result = self._run(comments, 'false')
        self.assertEqual([c.comment_id for c in result], ['100'])


if __name__ == '__main__':
    unittest.main()
