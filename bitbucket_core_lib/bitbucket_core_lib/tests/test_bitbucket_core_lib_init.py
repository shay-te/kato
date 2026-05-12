"""Coverage for ``BitbucketCoreLib`` constructor."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bitbucket_core_lib.bitbucket_core_lib.bitbucket_core_lib import BitbucketCoreLib


class BitbucketCoreLibInitTests(unittest.TestCase):
    def test_composes_pull_request_and_issue_clients(self) -> None:
        # Use a SimpleNamespace-shaped DictConfig stand-in. The
        # constructor reads core_lib.bitbucket_core_lib off the cfg
        # and uses .get(...) for the optional fields.
        bitbucket_cfg = SimpleNamespace(
            base_url='https://api.bitbucket.org',
            token='token',
            max_retries=3,
            workspace='ws',
        )
        bitbucket_cfg.get = lambda key, default='': {
            'api_email': 'me@example.com',
            'username': 'me',
            'repo_slug': 'repo',
        }.get(key, default)
        cfg = SimpleNamespace(core_lib=SimpleNamespace(
            bitbucket_core_lib=bitbucket_cfg,
        ))
        with patch(
            'bitbucket_core_lib.bitbucket_core_lib.bitbucket_core_lib.BitbucketClient',
        ) as pr_client_cls, patch(
            'bitbucket_core_lib.bitbucket_core_lib.bitbucket_core_lib.BitbucketIssuesClient',
        ) as issues_client_cls:
            lib = BitbucketCoreLib(cfg)
        self.assertIsNotNone(lib.pull_request)
        self.assertIsNotNone(lib.issue)
        # PR client uses api_email when present.
        pr_client_cls.assert_called_once()
        self.assertEqual(
            pr_client_cls.call_args.kwargs.get('username'),
            'me@example.com',
        )

    def test_falls_back_to_username_when_api_email_missing(self) -> None:
        bitbucket_cfg = SimpleNamespace(
            base_url='https://api.bitbucket.org',
            token='token',
            max_retries=3,
            workspace='ws',
        )
        bitbucket_cfg.get = lambda key, default='': {
            'api_email': '',
            'username': 'bob',
        }.get(key, default)
        cfg = SimpleNamespace(core_lib=SimpleNamespace(
            bitbucket_core_lib=bitbucket_cfg,
        ))
        with patch(
            'bitbucket_core_lib.bitbucket_core_lib.bitbucket_core_lib.BitbucketClient',
        ) as pr_client_cls, patch(
            'bitbucket_core_lib.bitbucket_core_lib.bitbucket_core_lib.BitbucketIssuesClient',
        ):
            BitbucketCoreLib(cfg)
        self.assertEqual(
            pr_client_cls.call_args.kwargs.get('username'),
            'bob',
        )


class ConfigModuleImportTests(unittest.TestCase):
    """The ``bitbucket_core_lib.config`` package init module — covered
    by importing it."""

    def test_config_package_imports_cleanly(self) -> None:
        from bitbucket_core_lib.bitbucket_core_lib import config  # noqa: F401


if __name__ == '__main__':
    unittest.main()
