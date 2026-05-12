"""Coverage for ``JiraCoreLib`` constructor."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jira_core_lib.jira_core_lib.jira_core_lib import JiraCoreLib


class JiraCoreLibInitTests(unittest.TestCase):
    def test_composes_issue_client(self) -> None:
        jira_cfg = SimpleNamespace(
            base_url='https://example.atlassian.net',
            token='token',
            email='me@example.com',
            max_retries=3,
        )
        cfg = SimpleNamespace(core_lib=SimpleNamespace(
            jira_core_lib=jira_cfg,
        ))
        with patch(
            'jira_core_lib.jira_core_lib.jira_core_lib.JiraClient',
        ) as client_cls:
            lib = JiraCoreLib(cfg)
        self.assertIsNotNone(lib.issue)
        client_cls.assert_called_once_with(
            'https://example.atlassian.net', 'token',
            'me@example.com', 3,
        )


if __name__ == '__main__':
    unittest.main()
