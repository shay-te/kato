import unittest
from unittest.mock import patch

from slack_core_lib.slack_core_lib.client.slack_client import SlackClient
from slack_core_lib.slack_core_lib.errors import SlackError
from slack_core_lib.slack_core_lib.tests.fakes import FakeSlack


class SlackClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.slack = FakeSlack()
        self.addCleanup(self.slack.close)
        self.client = SlackClient(' xoxb-1 ', base_url=self.slack.url)

    def test_posts_the_text_as_the_app_without_previews(self) -> None:
        self.client.post_message('C0123ABCD', '*done*')
        [request] = self.slack.requests
        self.assertEqual(request['path'], '/api/chat.postMessage')
        self.assertEqual(request['authorization'], 'Bearer xoxb-1')
        self.assertEqual(request['body'], {
            'channel': 'C0123ABCD', 'text': '*done*', 'unfurl_links': False, 'unfurl_media': False,
        })

    def test_each_refusal_says_what_to_do(self) -> None:
        cases = {
            'not_in_channel': '/invite',
            'channel_not_found': 'check the channel link',
            'invalid_auth': 'not valid',
            'missing_scope': 'chat:write',
            'something_new': 'Slack refused the message (something_new)',
        }
        for code, words in cases.items():
            self.slack.answer = (200, {'ok': False, 'error': code})
            with self.assertRaisesRegex(SlackError, words.replace('(', r'\(').replace(')', r'\)')):
                self.client.post_message('C0123ABCD', 'x')

    def test_rate_limits_and_answers_that_are_not_json(self) -> None:
        self.slack.answer = (429, {'ok': False})
        with self.assertRaisesRegex(SlackError, 'limiting'):
            self.client.post_message('C0123ABCD', 'x')
        self.slack.answer = (500, b'<html>down</html>')
        with self.assertRaisesRegex(SlackError, r'Slack refused the message \(500\)'):
            self.client.post_message('C0123ABCD', 'x')
        self.slack.answer = (200, ['ok'])
        with self.assertRaisesRegex(SlackError, r'\(200\)'):
            self.client.post_message('C0123ABCD', 'x')

    def test_nothing_is_sent_without_a_token_or_a_channel(self) -> None:
        with self.assertRaisesRegex(SlackError, 'no Slack token'):
            SlackClient('', base_url=self.slack.url).post_message('C0123ABCD', 'x')
        with self.assertRaisesRegex(SlackError, 'no Slack channel'):
            self.client.post_message('  ', 'x')
        self.assertEqual(self.slack.requests, [])

    def test_slack_that_cannot_be_reached(self) -> None:
        with patch.object(self.client.session, 'post', side_effect=OSError('connection refused')):
            with self.assertRaisesRegex(SlackError, 'could not reach Slack: connection refused'):
                self.client.post_message('C0123ABCD', 'x')


if __name__ == '__main__':
    unittest.main()
