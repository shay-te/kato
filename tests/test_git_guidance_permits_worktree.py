"""The agent must be told what git it MAY run, not only what it may not.

Reported symptom: asked to revert a file it had changed, the agent refused
until the operator repeated the request three times, reasoning that kato's
"never commit / never push" rule covered it. It does not — ``git restore``
is a working-tree operation and is deliberately absent from the floor's
deny list.

The cause was one-sided prompt text: both the floor rules and the
git-request block listed only prohibitions, so the model generalised "git is
kato's" from a page that never said otherwise. These tests pin the
permission being stated as explicitly as the denial.
"""

from __future__ import annotations

import unittest

from agent_core_lib.agent_core_lib.helpers.command_floor import (
    GIT_MUTATING_SUBCOMMANDS,
    prompt_floor_rules,
)
from kato_core_lib.helpers.git_request import agent_guidance_text


class FloorRulesStateWhatIsAllowedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = prompt_floor_rules()

    def test_working_tree_git_is_named_as_permitted(self) -> None:
        self.assertIn('git restore', self.rules)
        self.assertIn('git stash', self.rules)
        self.assertIn('git apply', self.rules)

    def test_reverting_a_file_is_called_ordinary_work(self) -> None:
        self.assertIn('ordinary work', self.rules)
        self.assertIn('do not report yourself blocked', self.rules)

    def test_the_permitted_verbs_are_genuinely_not_denied(self) -> None:
        """The prose must not promise something the denylist blocks."""
        for verb in ('restore', 'stash', 'apply'):
            self.assertNotIn(verb, GIT_MUTATING_SUBCOMMANDS)

    def test_the_hard_limits_are_still_stated(self) -> None:
        # The permission must not have softened the denial.
        for verb in ('push', 'commit', 'reset', 'checkout'):
            self.assertIn(verb, self.rules)

    def test_whole_tree_restore_is_discouraged(self) -> None:
        # ``git restore .`` is caught by the content-aware guard, so the
        # prompt steers to path-scoped use rather than promising it works.
        self.assertIn('Scope it to the paths concerned', self.rules)


class GitRequestGuidanceLeadsWithWhatNeedsNoRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = agent_guidance_text()

    def test_it_opens_with_the_permitted_operations(self) -> None:
        # Order matters: leading with the request machinery is what taught
        # the agent that git in general is kato's.
        self.assertTrue(self.text.startswith('GIT YOU JUST DO'))

    def test_restore_is_named_as_needing_no_approval(self) -> None:
        self.assertIn('git restore <path>', self.text)
        self.assertIn('no request and no approval', self.text)

    def test_it_says_the_publish_rule_is_not_about_the_working_tree(self) -> None:
        """The exact misreading the operator hit."""
        self.assertIn('say nothing about the working tree', self.text)

    def test_it_tells_the_agent_to_act_once_without_checking(self) -> None:
        self.assertIn('without checking first', self.text)

    def test_the_publish_guarantee_is_intact(self) -> None:
        self.assertIn('Pushing and opening the pull request are NOT available', self.text)
        self.assertIn('Done button', self.text)

    def test_kato_owned_operations_are_still_routed_through_the_channel(self) -> None:
        self.assertIn('git_request.json', self.text)
        for operation in ('commit', 'create_branch', 'switch_branch'):
            self.assertIn(operation, self.text)


if __name__ == '__main__':
    unittest.main()
