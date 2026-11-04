"""A task marked done is posted to the Slack channel set in Settings, with its PRs.

Real WorkspaceService (what the task is read from), the real ReleaseNoteSources,
the real Slack client talking HTTP to a local stand-in for Slack
(``slack_core_lib``'s FakeSlack); only the pull request lookup is a stub.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from slack_core_lib.slack_core_lib.client.slack_client import SlackClient
from slack_core_lib.slack_core_lib.tests.fakes import FakeSlack

from kato_core_lib.data_layers.data.release_note_source import ReleaseNoteSource
from kato_core_lib.data_layers.service.done_announcements import DoneAnnouncements
from kato_core_lib.data_layers.service.release_note_sources import ReleaseNoteSources
from kato_core_lib.data_layers.service.slack_done_announcer import SlackDoneAnnouncer, done_message
from kato_core_lib.helpers.slack_done_settings_utils import (
    SLACK_DONE_CHANNEL_KEY,
    SLACK_DONE_TOKEN_KEY,
    SlackDoneSettings,
    slack_done_settings,
)
from tests.chaos_lib import build_real_workspace_service

CHANNEL = 'https://acme.slack.com/archives/C0RELEASE1'


class _Links(object):
    def __init__(self, links) -> None:
        self.result = links
        self.asked: list[ReleaseNoteSource] = []

    def links(self, source):
        self.asked.append(source)
        return self.result


class SlackDoneAnnouncerTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='slack-done-')
        self.addCleanup(tmp.cleanup)
        self.workspaces = build_real_workspace_service(Path(tmp.name))
        self.workspaces.create(task_id='UNA-1', task_summary='Fix the login bug',
                               repository_ids=['backend', 'client'])
        self.slack = FakeSlack()
        self.addCleanup(self.slack.close)
        self.links = _Links([('backend', 'https://git.example/backend/pull/7'),
                             ('client', 'https://git.example/client/pull/8')])
        self.settings = SlackDoneSettings(channel_link=CHANNEL, token='xoxb-1')
        self.deferred: list = []
        self.logger = MagicMock()
        self.announcer = SlackDoneAnnouncer(
            sources=ReleaseNoteSources(workspace_manager=self.workspaces, task_service=MagicMock()),
            links=self.links,
            settings=lambda: self.settings,
            client_for=lambda token: SlackClient(token, base_url=self.slack.url),
            background=self.deferred.append,
            logger=self.logger,
        )

    def _done(self, task_id='UNA-1') -> None:
        self.announcer.task_done(task_id)
        for work in self.deferred:
            work()

    def test_the_channel_gets_the_task_and_every_pull_request(self) -> None:
        self._done()
        [request] = self.slack.requests
        self.assertEqual(request['authorization'], 'Bearer xoxb-1')
        self.assertEqual(request['body']['channel'], 'C0RELEASE1')
        self.assertEqual(request['body']['text'], '\n'.join([
            '✅ *UNA-1* is done — Fix the login bug',
            '• <https://git.example/backend/pull/7|backend>',
            '• <https://git.example/client/pull/8|client>',
        ]))

    def test_the_task_is_read_before_the_forget_flow_deletes_its_workspace(self) -> None:
        self.workspaces.add_tag('UNA-1', 'kato:branch:feature/login')
        self.announcer.task_done('UNA-1')
        self.workspaces.delete('UNA-1')  # the forget flow, right after done
        self.deferred[0]()
        self.assertEqual(self.links.asked[0].branch, 'feature/login')  # PRs found by the named branch
        self.assertIn('Fix the login bug', self.slack.requests[0]['body']['text'])

    def test_not_set_up_sends_nothing_and_half_set_up_says_what_is_missing(self) -> None:
        self.settings = SlackDoneSettings()
        self._done()
        self.settings = SlackDoneSettings(channel_link=CHANNEL)
        self._done()
        self.settings = SlackDoneSettings(channel_link='releases', token='xoxb-1')
        self._done()
        self.assertEqual(self.slack.requests, [])
        warnings = [call.args[2] for call in self.logger.warning.call_args_list]
        self.assertEqual(warnings, ['the Slack token is missing', 'the Slack channel link is not a channel link'])

    def test_a_slack_that_refuses_is_logged_in_words_and_never_fails_the_done(self) -> None:
        self.slack.answer = (200, {'ok': False, 'error': 'not_in_channel'})
        self._done()
        message = self.logger.error.call_args.args
        self.assertIn('not posted to Slack', message[0])
        self.assertIn('invite it there', str(message[2]))

    def test_anything_unexpected_is_logged_never_raised(self) -> None:
        self.announcer._sources = SimpleNamespace(read=MagicMock(side_effect=RuntimeError('disk gone')))
        self._done()
        self.logger.exception.assert_called()
        self.announcer._sources = ReleaseNoteSources(workspace_manager=self.workspaces, task_service=MagicMock())
        self.links.links = MagicMock(side_effect=RuntimeError('host down'))
        self._done()
        self.assertEqual(self.logger.exception.call_count, 2)
        self.announcer.task_done('   ')  # nothing to announce
        self.assertEqual(self.slack.requests, [])


class DoneMessageTests(unittest.TestCase):
    def test_one_pull_request_and_none(self) -> None:
        source = ReleaseNoteSource(task_id='UNA-2', title='Add retry')
        self.assertEqual(done_message(source, [('PR', 'https://x/pull/1')]),
                         '✅ *UNA-2* is done — Add retry\n• <https://x/pull/1|pull request>')
        self.assertEqual(done_message(ReleaseNoteSource(task_id='UNA-3'), []),
                         '✅ *UNA-3* is done\n_No pull request was found for it._')

    def test_a_title_can_never_notify_the_channel(self) -> None:
        text = done_message(ReleaseNoteSource(task_id='UNA-4', title='Ping <!channel> & <@U1>'), [])
        self.assertIn('Ping &lt;!channel&gt; &amp; &lt;@U1&gt;', text)
        self.assertNotIn('<!channel>', text)


class DoneAnnouncementsTests(unittest.TestCase):
    def test_each_is_told_and_one_that_fails_never_stops_the_next(self) -> None:
        first = MagicMock()
        first.task_done.side_effect = RuntimeError('disk full')
        second = MagicMock()
        logger = MagicMock()
        DoneAnnouncements([first, None, second], logger=logger).task_done('UNA-1')
        first.task_done.assert_called_once_with('UNA-1')
        second.task_done.assert_called_once_with('UNA-1')
        logger.exception.assert_called_once()


class SettingsTests(unittest.TestCase):
    def test_read_fresh_from_settings_then_the_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'
            path.write_text(json.dumps({SLACK_DONE_CHANNEL_KEY: CHANNEL}))
            with patch.dict(os.environ, {'KATO_SETTINGS_FILE': str(path), SLACK_DONE_TOKEN_KEY: 'xoxb-shell'}):
                settings = slack_done_settings()
                self.assertEqual((settings.channel_link, settings.token, settings.wanted), (CHANNEL, 'xoxb-shell', True))
                path.write_text('{}')
                os.environ.pop(SLACK_DONE_TOKEN_KEY)
                self.assertFalse(slack_done_settings().wanted)  # a save applies at once

    def test_both_are_in_settings_and_apply_without_a_restart(self) -> None:
        from kato_core_lib.helpers.kato_settings_schema_utils import SETTINGS_SCHEMA
        from kato_webserver.app import _live_settings_keys

        fields = {field[0]: field[1] for section in SETTINGS_SCHEMA for field in section['fields']}
        self.assertEqual(fields[SLACK_DONE_CHANNEL_KEY], 'text')
        self.assertEqual(fields[SLACK_DONE_TOKEN_KEY], 'secret')  # never shown back
        self.assertLessEqual({SLACK_DONE_CHANNEL_KEY, SLACK_DONE_TOKEN_KEY}, _live_settings_keys())


if __name__ == '__main__':
    unittest.main()
