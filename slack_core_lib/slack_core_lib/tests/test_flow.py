"""A to Z: a channel link a person copied → the message in that channel."""

import unittest

from slack_core_lib.slack_core_lib.client.slack_client import SlackClient
from slack_core_lib.slack_core_lib.errors import SlackError
from slack_core_lib.slack_core_lib.helpers.channel_link import channel_from_link
from slack_core_lib.slack_core_lib.tests.fakes import FakeSlack


class FlowTests(unittest.TestCase):
    def test_a_copied_link_and_a_bot_token_post_to_that_channel(self) -> None:
        slack = FakeSlack()
        self.addCleanup(slack.close)
        channel = channel_from_link('https://acme.slack.com/archives/C0RELEASE1')
        SlackClient('xoxb-token', base_url=slack.url).post_message(channel, 'UNA-1 is done')
        self.assertEqual(slack.requests[0]['body']['channel'], 'C0RELEASE1')

    def test_an_app_not_in_the_channel_is_told_to_be_invited(self) -> None:
        slack = FakeSlack()
        self.addCleanup(slack.close)
        slack.answer = (200, {'ok': False, 'error': 'not_in_channel'})
        with self.assertRaisesRegex(SlackError, 'invite it there'):
            SlackClient('xoxb-token', base_url=slack.url).post_message(
                channel_from_link('https://app.slack.com/client/T01/C0RELEASE1'), 'x',
            )


if __name__ == '__main__':
    unittest.main()
