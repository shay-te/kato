"""Cross-path @-mention agreement: the issue path and the review path must
never drift apart again.

WHY THIS FILE EXISTS
--------------------
The same operator bug — "I tagged another developer and kato went and did the
work anyway" — was reported FOUR times. Every time it was fixed on one path
and left broken on the other, because kato filters mentions in two completely
independent places:

  * ISSUE comments  → ``IssueClientBase._comment_addressed_elsewhere``, used by
    all five platform clients to decide what lands in the task description.
  * PR REVIEW comments → ``ReviewCommentService._review_comment_not_for_kato``,
    used by the scan loop to decide what the agent acts on.

They have separate extractors, separate identity resolution and separate call
sites, so a fix to one is invisible to the other. The most recent instance:
the review path moved to the brace-aware union extractor while the issue path
stayed on plain-``@login``-only, leaving YouTrack / GitHub / GitLab issues
silently failing open on ``@{Dave Smith}``.

So this file runs ONE fixture set through BOTH paths and asserts they agree.
It is deliberately behavioral (real client objects, real service function) —
not a unit test of the shared helper, which cannot catch a caller that stops
using it.

Run it against the pre-fix code and ``brace_human_name`` / ``brace_human_uuid``
fail on the issue path (they extracted no mention at all, so the filter kept a
comment addressed to a human) while passing on the review path — which is
exactly the drift being locked out.
"""

from __future__ import annotations

import unittest

from bitbucket_core_lib.bitbucket_core_lib.client.bitbucket_issues_client import (
    BitbucketIssuesClient,
)
from github_core_lib.github_core_lib.client.github_issues_client import (
    GitHubIssuesClient,
)
from gitlab_core_lib.gitlab_core_lib.client.gitlab_issues_client import (
    GitLabIssuesClient,
)
from jira_core_lib.jira_core_lib.client.jira_client import JiraClient
from kato_core_lib.data_layers.service.review_comment_service import (
    ReviewCommentService,
)
from youtrack_core_lib.youtrack_core_lib.client.youtrack_client import YouTrackClient
from tests.review_mention_policy_support import legacy_mention_policy


# The bot's identity, spelled the same way on both paths so the two are
# directly comparable. Generic on purpose — the platform libs must stay
# kato-free, and reusing their vocabulary keeps the fixtures portable.
BOT = 'bot_user'

# (label, comment body, tags_someone_other_than_the_bot)
#
# ``True``  → the comment belongs to a human; kato must stay out of it.
# ``False`` → kato may act (tags nobody, or tags the bot itself).
FIXTURES: tuple[tuple[str, str, bool], ...] = (
    ('mention_free',       'just a general note about the approach',   False),
    ('plain_human',        '@dave please take a look',                 True),
    ('brace_human_name',   '@{Dave Smith} please take a look',         True),
    ('brace_human_uuid',   '@{557058:dave-uuid-here} please look',     True),
    ('plain_bot',          f'@{BOT} please fix the null check',        False),
    ('brace_bot',          f'@{{{BOT}}} please fix the null check',    False),
    ('human_and_bot',      f'@dave and @{BOT} should both look',       False),
    ('code_annotation',    'should this method use @Override?',        False),
    ('decorator',          'wrap it in @property maybe',               False),
    ('email_is_not_a_tag', 'ask dave@example.com about it',            False),
    ('multiple_humans',    '@dave and @carol please sync',             True),
    ('mid_sentence_human', 'I think @dave owns this bit now',          True),
)


def _issue_clients() -> dict[str, object]:
    """One live client per issue platform, all pinned to the same bot login.

    Constructed directly (no network is touched — only the pure mention
    predicate is exercised) so the test sees exactly what production sees,
    including each platform's own ``_extract_comment_mentions`` override.
    """
    return {
        'youtrack': YouTrackClient('https://yt.example', 't', 30, bot_login=BOT),
        'jira': JiraClient('https://x.atlassian.net', 't', 'b@x.com', 3, bot_login=BOT),
        'github': GitHubIssuesClient(
            'https://api.github.com', 't', 'owner', 'repo', 3, bot_login=BOT,
        ),
        'gitlab': GitLabIssuesClient('https://gitlab.example', 't', 'g/p', 3, bot_login=BOT),
        'bitbucket': BitbucketIssuesClient(
            'https://api.bitbucket.org/2.0', 't', 'ws', 'repo', 3, bot_login=BOT,
        ),
    }


def _review_skips_with_identities(body: str, identities: tuple) -> bool:
    """Review-path verdict for an explicit identity set, legacy rule pinned."""
    with legacy_mention_policy():
        return ReviewCommentService._review_comment_not_for_kato(body, identities)


def _review_skips(body: str) -> bool:
    """The review path's verdict under the LEGACY (mention-optional) rule.

    Both paths now also support a stricter "must @mention kato" policy, but
    the agreement this file locks in is about MENTION EXTRACTION — whether
    each path can see an ``@dave`` or an ``@{Dave Smith}`` at all. Pinning the
    legacy rule keeps that the only variable; the strict policy has its own
    suites (test_review_comment_require_mention, CommentPolicyTests).
    """
    with legacy_mention_policy():
        return ReviewCommentService._review_comment_not_for_kato(body, (BOT,))


class IssuePathVerdictTests(unittest.TestCase):
    """Every platform's issue-comment filter, against the shared fixtures."""

    def test_every_platform_agrees_with_the_expected_verdict(self) -> None:
        failures: list[str] = []
        for platform, client in _issue_clients().items():
            for label, body, expected in FIXTURES:
                actual = client._comment_addressed_elsewhere(body)
                if actual != expected:
                    failures.append(
                        f'{platform}/{label}: expected skip={expected}, '
                        f'got {actual} for {body!r}',
                    )
        self.assertEqual(failures, [], '\n' + '\n'.join(failures))


class ReviewPathVerdictTests(unittest.TestCase):
    """The PR-review filter, against the very same fixtures."""

    def test_review_path_agrees_with_the_expected_verdict(self) -> None:
        failures = [
            f'{label}: expected skip={expected}, got {_review_skips(body)} for {body!r}'
            for label, body, expected in FIXTURES
            if _review_skips(body) != expected
        ]
        self.assertEqual(failures, [], '\n' + '\n'.join(failures))


class CrossPathAgreementTests(unittest.TestCase):
    """THE point of this file: the two paths must reach the SAME verdict.

    Asserted independently of the expected-value table above, so this still
    catches a drift that happens to move both paths off the table together.
    """

    def test_issue_and_review_paths_never_disagree(self) -> None:
        disagreements: list[str] = []
        for platform, client in _issue_clients().items():
            for label, body, _ in FIXTURES:
                issue_verdict = client._comment_addressed_elsewhere(body)
                review_verdict = _review_skips(body)
                if issue_verdict != review_verdict:
                    disagreements.append(
                        f'{label}: issue({platform})={issue_verdict} but '
                        f'review={review_verdict} for {body!r}',
                    )
        self.assertEqual(
            disagreements, [],
            'The issue-comment and PR-review mention filters disagree. That '
            'divergence is how the same "kato worked a comment I addressed to '
            'a teammate" bug survived four fixes.\n' + '\n'.join(disagreements),
        )

    def test_brace_encoded_mentions_are_seen_by_both_paths(self) -> None:
        # The specific regression: the issue path was plain-``@login``-only,
        # so a brace mention extracted NOTHING and the filter FAILED OPEN.
        for body in ('@{Dave Smith} look at this', '@{557058:dave-uuid} look'):
            self.assertTrue(_review_skips(body), body)
            for platform, client in _issue_clients().items():
                self.assertTrue(
                    client._comment_addressed_elsewhere(body),
                    f'{platform} failed open on a brace-encoded human mention: {body!r}',
                )


class UnresolvableIdentityDivergenceTests(unittest.TestCase):
    """The ONE place the two paths deliberately differ — pinned, not asserted equal.

    When the bot's identity can't be resolved at all, the paths disagree BY
    DESIGN, and each direction was a deliberate operator decision:

      * review path → FAIL CLOSED. A comment that tags people is a human's to
        answer even when kato can't confirm it isn't the tagged party. The
        operator explicitly chose under-action here.
      * issue path  → FAIL OPEN. "Skip" there means "drop this comment from the
        task description", so failing closed would silently discard
        instructions genuinely addressed to the bot.

    This test exists so the asymmetry is a recorded decision rather than an
    accident someone "fixes" into a regression. If the product rule changes,
    change it here first.
    """

    def test_review_path_fails_closed_with_no_identity(self) -> None:
        self.assertTrue(
            _review_skips_with_identities('@dave ping', ()),
        )

    def test_issue_path_fails_open_with_no_identity(self) -> None:
        client = YouTrackClient('https://yt.example', 't', 30, bot_login='')
        client._resolved_bot_logins = ()
        client._fetch_current_user_logins = lambda: ()
        self.assertFalse(client._comment_addressed_elsewhere('@dave ping'))

    def test_both_paths_still_ignore_mention_free_comments(self) -> None:
        # Whatever the identity situation, a comment tagging nobody is never
        # "someone else's" — that is the one rule both paths share absolutely.
        client = YouTrackClient('https://yt.example', 't', 30, bot_login='')
        client._fetch_current_user_logins = lambda: ()
        self.assertFalse(client._comment_addressed_elsewhere('plain note'))
        self.assertFalse(
            _review_skips_with_identities('plain note', ()),
        )


if __name__ == '__main__':
    unittest.main()
