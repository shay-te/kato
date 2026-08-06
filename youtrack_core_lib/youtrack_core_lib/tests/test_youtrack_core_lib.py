"""Tests for YouTrackCoreLib constructor and wiring."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from core_lib.core_lib import CoreLib

from youtrack_core_lib.youtrack_core_lib.client.youtrack_client import YouTrackClient
from youtrack_core_lib.youtrack_core_lib.youtrack_core_lib import YouTrackCoreLib


def _cfg(
    base_url='https://youtrack.example',
    token='tok',
    max_retries=3,
    operational_comment_prefixes=None,
):
    youtrack_cfg = MagicMock()
    youtrack_cfg.base_url = base_url
    youtrack_cfg.token = token
    youtrack_cfg.max_retries = max_retries
    youtrack_cfg.operational_comment_prefixes = operational_comment_prefixes

    cfg = MagicMock()
    cfg.core_lib.youtrack_core_lib = youtrack_cfg
    return cfg


class CommentPolicyWiringTests(unittest.TestCase):
    """The host's comment settings must reach the client, not stop at config.

    A policy that parses correctly but never gets passed through is the
    failure mode that looks fine in the settings UI and changes nothing.
    """

    def test_policy_flows_from_config_to_the_client(self):
        cfg = _cfg()
        cfg.core_lib.youtrack_core_lib.include_comments = True
        cfg.core_lib.youtrack_core_lib.require_bot_mention = True
        cfg.core_lib.youtrack_core_lib.assignee = 'agent_bot'
        client = YouTrackCoreLib(cfg).issue
        self.assertTrue(client._include_comments)
        self.assertTrue(client._require_bot_mention)
        # …and the rule it produces: only a comment tagging the bot survives.
        self.assertFalse(client._should_skip_comment('@agent_bot please fix'))
        self.assertTrue(client._should_skip_comment('untagged chatter'))
        self.assertTrue(client._should_skip_comment('@alice please fix'))

    def test_comments_disabled_skips_everything(self):
        cfg = _cfg()
        cfg.core_lib.youtrack_core_lib.include_comments = False
        cfg.core_lib.youtrack_core_lib.require_bot_mention = True
        cfg.core_lib.youtrack_core_lib.assignee = 'agent_bot'
        client = YouTrackCoreLib(cfg).issue
        self.assertTrue(client._should_skip_comment('@agent_bot please fix'))

    def test_string_config_values_are_coerced(self):
        cfg = _cfg()
        cfg.core_lib.youtrack_core_lib.include_comments = 'false'
        cfg.core_lib.youtrack_core_lib.require_bot_mention = 'true'
        cfg.core_lib.youtrack_core_lib.assignee = 'agent_bot'
        client = YouTrackCoreLib(cfg).issue
        self.assertFalse(client._include_comments)
        self.assertTrue(client._require_bot_mention)


class YouTrackCoreLibInheritanceTests(unittest.TestCase):
    def test_is_core_lib_subclass(self):
        self.assertTrue(issubclass(YouTrackCoreLib, CoreLib))


class YouTrackCoreLibConstructionTests(unittest.TestCase):
    def test_issue_attribute_is_youtrack_client(self):
        lib = YouTrackCoreLib(_cfg())
        self.assertIsInstance(lib.issue, YouTrackClient)

    def test_base_url_passed(self):
        lib = YouTrackCoreLib(_cfg(base_url='https://yt.example'))
        self.assertIn('yt.example', lib.issue.base_url)

    def test_token_in_headers(self):
        lib = YouTrackCoreLib(_cfg(token='my-secret'))
        self.assertEqual(lib.issue.headers.get('Authorization'), 'Bearer my-secret')

    def test_max_retries_passed(self):
        lib = YouTrackCoreLib(_cfg(max_retries=5))
        self.assertEqual(lib.issue.max_retries, 5)

    def test_operational_comment_prefixes_default_empty(self):
        lib = YouTrackCoreLib(_cfg())
        self.assertEqual(lib.issue._operational_comment_prefixes, ())

    def test_operational_comment_prefixes_passed_from_config(self):
        lib = YouTrackCoreLib(_cfg(operational_comment_prefixes=['Prefix A:', 'Prefix B:']))
        self.assertIn('Prefix A:', lib.issue._operational_comment_prefixes)
        self.assertIn('Prefix B:', lib.issue._operational_comment_prefixes)

    def test_operational_comment_prefixes_are_tuple(self):
        lib = YouTrackCoreLib(_cfg(operational_comment_prefixes=['X:']))
        self.assertIsInstance(lib.issue._operational_comment_prefixes, tuple)


if __name__ == '__main__':
    unittest.main()
