from __future__ import annotations

import unittest

from repository_core_lib.repository_core_lib.platform import Platform


class PlatformTests(unittest.TestCase):
    def test_detects_github_from_base_url(self) -> None:
        self.assertEqual(
            Platform.from_base_url('https://api.github.com'),
            Platform.GITHUB,
        )

    def test_detects_gitlab_from_base_url(self) -> None:
        self.assertEqual(
            Platform.from_base_url('https://gitlab.example/api/v4'),
            Platform.GITLAB,
        )

    def test_detects_bitbucket_from_base_url(self) -> None:
        self.assertEqual(
            Platform.from_base_url('https://api.bitbucket.org/2.0'),
            Platform.BITBUCKET,
        )

    def test_rejects_unknown_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, 'unsupported repository provider'):
            Platform.from_base_url('https://code.example.com/api')
