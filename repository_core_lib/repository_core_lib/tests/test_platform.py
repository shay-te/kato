from __future__ import annotations

import unittest

from repository_core_lib.repository_core_lib.platform import Platform


class PlatformEnumTests(unittest.TestCase):
    def test_github_value(self):
        self.assertEqual(Platform.GITHUB.value, 'github')

    def test_gitlab_value(self):
        self.assertEqual(Platform.GITLAB.value, 'gitlab')

    def test_bitbucket_value(self):
        self.assertEqual(Platform.BITBUCKET.value, 'bitbucket')

    def test_all_three_members_exist(self):
        self.assertSetEqual({p.name for p in Platform}, {'GITHUB', 'GITLAB', 'BITBUCKET'})

    def test_members_are_distinct(self):
        self.assertNotEqual(Platform.GITHUB, Platform.GITLAB)
        self.assertNotEqual(Platform.GITHUB, Platform.BITBUCKET)
        self.assertNotEqual(Platform.GITLAB, Platform.BITBUCKET)


class PlatformFromBaseUrlTests(unittest.TestCase):
    # GitHub

    def test_detects_github_from_api_url(self):
        self.assertEqual(Platform.from_base_url('https://api.github.com'), Platform.GITHUB)

    def test_detects_github_from_base_domain(self):
        self.assertEqual(Platform.from_base_url('https://github.com'), Platform.GITHUB)

    def test_detects_github_from_url_with_path(self):
        self.assertEqual(
            Platform.from_base_url('https://api.github.com/repos/owner/repo'),
            Platform.GITHUB,
        )

    def test_detects_github_case_insensitive(self):
        self.assertEqual(Platform.from_base_url('https://API.GITHUB.COM'), Platform.GITHUB)

    # GitLab

    def test_detects_gitlab_from_example_host(self):
        self.assertEqual(Platform.from_base_url('https://gitlab.example/api/v4'), Platform.GITLAB)

    def test_detects_gitlab_from_gitlab_com(self):
        self.assertEqual(Platform.from_base_url('https://gitlab.com/api/v4'), Platform.GITLAB)

    def test_detects_gitlab_self_hosted_path(self):
        self.assertEqual(
            Platform.from_base_url('https://code.company.com/gitlab/api/v4'),
            Platform.GITLAB,
        )

    def test_detects_gitlab_case_insensitive(self):
        self.assertEqual(Platform.from_base_url('https://GITLAB.EXAMPLE.COM'), Platform.GITLAB)

    # Bitbucket

    def test_detects_bitbucket_from_api_url(self):
        self.assertEqual(
            Platform.from_base_url('https://api.bitbucket.org/2.0'),
            Platform.BITBUCKET,
        )

    def test_detects_bitbucket_from_base_domain(self):
        self.assertEqual(Platform.from_base_url('https://bitbucket.org'), Platform.BITBUCKET)

    def test_detects_bitbucket_case_insensitive(self):
        self.assertEqual(Platform.from_base_url('https://API.BITBUCKET.ORG'), Platform.BITBUCKET)

    # Unsupported

    def test_rejects_unknown_provider(self):
        with self.assertRaisesRegex(ValueError, 'unsupported repository provider'):
            Platform.from_base_url('https://code.example.com/api')

    def test_rejects_empty_string(self):
        with self.assertRaises(ValueError):
            Platform.from_base_url('')

    def test_rejects_azure_devops(self):
        with self.assertRaisesRegex(ValueError, 'unsupported repository provider'):
            Platform.from_base_url('https://dev.azure.com/org/project')

    def test_error_message_contains_base_url(self):
        bad_url = 'https://unknown.provider.io/api'
        with self.assertRaises(ValueError) as ctx:
            Platform.from_base_url(bad_url)
        self.assertIn(bad_url, str(ctx.exception))
