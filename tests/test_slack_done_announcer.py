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
from kato_core_lib.data_layers.service.slack_posted_tasks import SlackPostedTasks
from kato_core_lib.data_layers.service.slack_done_announcer import SlackDoneAnnouncer, done_message
from kato_core_lib.helpers.agent_name_utils import AGENT_NAME_KEY
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
        self.posted = SlackPostedTasks(Path(tmp.name) / 'slack_posted.json')
        self.announcer = SlackDoneAnnouncer(
            sources=ReleaseNoteSources(workspace_manager=self.workspaces, task_service=MagicMock()),
            links=self.links,
            posted=self.posted,
            settings=lambda: self.settings,
            client_for=lambda token: SlackClient(token, base_url=self.slack.url),
            background=self.deferred.append,
            signed_by=lambda: self.agent,
            logger=self.logger,
        )
        self.agent = "Shay Tessler's kato"

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
            # Whose agent did it: several developers' katos post to one channel.
            "Finished by *Shay Tessler's kato*",
            '• backend: <https://git.example/backend/pull/7>',
            '• client: <https://git.example/client/pull/8>',
        ]))

    def test_the_name_is_read_for_each_post_so_a_settings_save_applies_at_once(self) -> None:
        self.agent = 'Dana (laptop)'
        self._done()
        self.assertIn('Finished by *Dana (laptop)*', self.slack.requests[0]['body']['text'])

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
        self.assertEqual(len(warnings), 2)
        self.assertIn('needs the Slack app\'s bot token', warnings[0])
        self.assertIn('Incoming Webhook URL instead', warnings[0])
        self.assertIn('neither a channel link nor an Incoming Webhook URL', warnings[1])

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
                         '✅ *UNA-2* is done — Add retry\n• <https://x/pull/1>')  # the whole link, shown
        self.assertEqual(done_message(ReleaseNoteSource(task_id='UNA-3'), []),
                         '✅ *UNA-3* is done\n_No pull request was found for it._')
        # A repository's name is text like any other: it can never become a mention.
        self.assertEqual(done_message(ReleaseNoteSource(task_id='UNA-4'), [('a<!channel>&b', 'https://x/pull/2')]),
                         '✅ *UNA-4* is done\n• a&lt;!channel&gt;&amp;b: <https://x/pull/2>')

    def test_the_signature_is_escaped_and_left_out_when_there_is_none(self) -> None:
        source = ReleaseNoteSource(task_id='UNA-5', title='Add retry')
        self.assertEqual(
            done_message(source, [('PR', 'https://x/pull/1')], ' <!here> & co '),
            '✅ *UNA-5* is done — Add retry\nFinished by *&lt;!here&gt; &amp; co*\n• <https://x/pull/1>',
        )
        self.assertNotIn('Finished by', done_message(source, [], '  '))

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
        self.assertEqual(fields[SLACK_DONE_CHANNEL_KEY], 'secret')  # a webhook URL is a credential
        self.assertEqual(fields[SLACK_DONE_TOKEN_KEY], 'secret')  # never shown back
        self.assertEqual(fields[AGENT_NAME_KEY], 'text')
        self.assertLessEqual({SLACK_DONE_CHANNEL_KEY, SLACK_DONE_TOKEN_KEY, AGENT_NAME_KEY}, _live_settings_keys())


if __name__ == '__main__':
    unittest.main()


class DoneButtonAnnouncesTests(SlackDoneAnnouncerTests):
    """The Done button (push, PR, In Review) posts NOW and says what happened;
    a webhook URL alone is enough; a task is posted once."""

    HOOK = 'https://hooks.slack.com/services/T0001/B0002/sEcReTpart123'

    def _webhook(self):
        from slack_core_lib.slack_core_lib.client.slack_webhook import SlackWebhook
        self.slack.answer = (200, b'ok')
        self.announcer._webhook_for = lambda url: SlackWebhook(url, base_url=self.slack.url.rsplit('/api', 1)[0])
        self.settings = SlackDoneSettings(channel_link=self.HOOK)

    def test_a_webhook_url_alone_posts_and_done_says_so(self) -> None:
        self._webhook()
        self.assertEqual(self.announcer.announce('UNA-1'), {'status': 'posted', 'error': ''})
        [sent] = self.slack.requests
        self.assertEqual(sent['path'], '/services/T0001/B0002/sEcReTpart123')
        self.assertIn('*UNA-1* is done — Fix the login bug', sent['body']['text'])
        self.assertIn('• backend: <https://git.example/backend/pull/7>', sent['body']['text'])

    def test_a_task_is_posted_once_whichever_way_it_got_done(self) -> None:
        self._webhook()
        self.announcer.announce('UNA-1')
        self.assertEqual(self.announcer.announce('una-1'), {'status': 'already_posted', 'error': ''})
        self._done('UNA-1')  # the tracker's Done later: not again
        self.assertEqual(len(self.slack.requests), 1)

    def test_what_the_done_toast_is_told_when_it_was_not_posted(self) -> None:
        self.settings = SlackDoneSettings()
        self.assertEqual(self.announcer.announce('UNA-1'), {'status': 'not_set_up', 'error': ''})
        self.settings = SlackDoneSettings(channel_link=CHANNEL)
        outcome = self.announcer.announce('UNA-1')
        self.assertEqual(outcome['status'], 'failed')
        self.assertIn('Incoming Webhook URL instead', outcome['error'])
        self._webhook()
        self.slack.answer = (404, b'no_service')
        self.assertEqual(self.announcer.announce('UNA-1')['status'], 'failed')
        self.assertFalse(self.posted.has('UNA-1'))  # a failed post is tried again next time
        self.slack.answer = (200, b'ok')
        self.assertEqual(self.announcer.announce('UNA-1')['status'], 'posted')

    def test_nothing_unexpected_ever_escapes(self) -> None:
        self._webhook()
        self.links.result = None  # the PR lookup breaks
        self.links.links = lambda source: (_ for _ in ()).throw(RuntimeError('provider down'))
        self.assertEqual(self.announcer.announce('UNA-1'), {'status': 'failed', 'error': 'provider down'})
        self.announcer._settings = lambda: (_ for _ in ()).throw(RuntimeError('settings unreadable'))
        self.assertEqual(self.announcer.announce('UNA-1'), {'status': 'failed', 'error': 'settings unreadable'})
        self.assertEqual(self.announcer.announce(''), {'status': 'failed', 'error': 'settings unreadable'})


class SlackPostedTasksTests(unittest.TestCase):
    def test_marked_once_case_blind_and_a_bad_file_is_empty(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'posted.json'
        posted = SlackPostedTasks(path, clock=lambda: 7.0)
        self.assertFalse(posted.has('UNA-1'))
        posted.mark('UNA-1')
        self.assertTrue(SlackPostedTasks(path).has(' una-1 '))
        path.write_text('[1]')
        self.assertFalse(posted.has('UNA-1'))
        path.write_text('nope')
        self.assertFalse(posted.has('UNA-1'))

    def test_the_default_place_is_under_kato_home(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with unittest.mock.patch.dict(os.environ, {'KATO_SLACK_POSTED_PATH': str(Path(tmp.name) / 'p.json')}):
            SlackPostedTasks().mark('UNA-2')
        self.assertTrue((Path(tmp.name) / 'p.json').exists())
