"""Full coverage for platform.py — Platform enum values and membership."""
from __future__ import annotations

import unittest

from task_core_lib.task_core_lib.platform import Platform


class PlatformEnumTests(unittest.TestCase):
    def test_all_eight_members_exist(self) -> None:
        names = {m.name for m in Platform}
        expected = {
            'YOUTRACK', 'JIRA',
            'GITHUB', 'GITHUB_ISSUES',
            'GITLAB', 'GITLAB_ISSUES',
            'BITBUCKET', 'BITBUCKET_ISSUES',
        }
        self.assertEqual(names, expected)

    def test_member_count_is_eight(self) -> None:
        self.assertEqual(len(Platform), 8)

    def test_youtrack_value(self) -> None:
        self.assertEqual(Platform.YOUTRACK.value, 'youtrack')

    def test_jira_value(self) -> None:
        self.assertEqual(Platform.JIRA.value, 'jira')

    def test_github_value(self) -> None:
        self.assertEqual(Platform.GITHUB.value, 'github')

    def test_github_issues_value(self) -> None:
        self.assertEqual(Platform.GITHUB_ISSUES.value, 'github_issues')

    def test_gitlab_value(self) -> None:
        self.assertEqual(Platform.GITLAB.value, 'gitlab')

    def test_gitlab_issues_value(self) -> None:
        self.assertEqual(Platform.GITLAB_ISSUES.value, 'gitlab_issues')

    def test_bitbucket_value(self) -> None:
        self.assertEqual(Platform.BITBUCKET.value, 'bitbucket')

    def test_bitbucket_issues_value(self) -> None:
        self.assertEqual(Platform.BITBUCKET_ISSUES.value, 'bitbucket_issues')

    def test_lookup_by_value_youtrack(self) -> None:
        self.assertEqual(Platform('youtrack'), Platform.YOUTRACK)

    def test_lookup_by_value_jira(self) -> None:
        self.assertEqual(Platform('jira'), Platform.JIRA)

    def test_lookup_by_value_github(self) -> None:
        self.assertEqual(Platform('github'), Platform.GITHUB)

    def test_lookup_by_value_github_issues(self) -> None:
        self.assertEqual(Platform('github_issues'), Platform.GITHUB_ISSUES)

    def test_lookup_by_value_gitlab(self) -> None:
        self.assertEqual(Platform('gitlab'), Platform.GITLAB)

    def test_lookup_by_value_gitlab_issues(self) -> None:
        self.assertEqual(Platform('gitlab_issues'), Platform.GITLAB_ISSUES)

    def test_lookup_by_value_bitbucket(self) -> None:
        self.assertEqual(Platform('bitbucket'), Platform.BITBUCKET)

    def test_lookup_by_value_bitbucket_issues(self) -> None:
        self.assertEqual(Platform('bitbucket_issues'), Platform.BITBUCKET_ISSUES)

    def test_unknown_value_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            Platform('unknown_platform')

    def test_github_and_github_issues_are_distinct_members(self) -> None:
        self.assertIsNot(Platform.GITHUB, Platform.GITHUB_ISSUES)
        self.assertNotEqual(Platform.GITHUB, Platform.GITHUB_ISSUES)

    def test_gitlab_and_gitlab_issues_are_distinct_members(self) -> None:
        self.assertIsNot(Platform.GITLAB, Platform.GITLAB_ISSUES)
        self.assertNotEqual(Platform.GITLAB, Platform.GITLAB_ISSUES)

    def test_bitbucket_and_bitbucket_issues_are_distinct_members(self) -> None:
        self.assertIsNot(Platform.BITBUCKET, Platform.BITBUCKET_ISSUES)
        self.assertNotEqual(Platform.BITBUCKET, Platform.BITBUCKET_ISSUES)

    def test_members_are_hashable(self) -> None:
        mapping = {p: p.value for p in Platform}
        self.assertEqual(len(mapping), 8)

    def test_members_usable_as_dict_keys(self) -> None:
        d = {Platform.YOUTRACK: 'yt', Platform.JIRA: 'jira'}
        self.assertEqual(d[Platform.YOUTRACK], 'yt')
        self.assertEqual(d[Platform.JIRA], 'jira')


if __name__ == '__main__':
    unittest.main()
