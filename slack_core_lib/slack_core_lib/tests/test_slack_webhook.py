"""An Incoming Webhook: the URL alone posts to its channel — and stays secret."""

import unittest
from unittest import mock

from slack_core_lib.slack_core_lib.client.slack_webhook import SlackWebhook
from slack_core_lib.slack_core_lib.errors import SlackError
from slack_core_lib.slack_core_lib.helpers.channel_link import webhook_url
from slack_core_lib.slack_core_lib.tests.fakes import FakeSlack

HOOK = 'https://hooks.slack.com/services/T0001/B0002/sEcReTpart123'


class WebhookUrlTests(unittest.TestCase):
    def test_a_webhook_url_and_what_is_not_one(self) -> None:
        self.assertEqual(webhook_url(f' {HOOK}/ '), HOOK)
        self.assertEqual(webhook_url('https://hooks.slack-gov.com/services/T1/B2/abc'),
                         'https://hooks.slack-gov.com/services/T1/B2/abc')
        for other in ('http://hooks.slack.com/services/T1/B2/abc', 'https://hooks.slack.com.evil.example/services/T1/B2/abc',
                      'https://hooks.slack.com/services/T1/B2', 'https://acme.slack.com/archives/C0123456789', '', None):
            self.assertEqual(webhook_url(other), '', other)


class SlackWebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.slack = FakeSlack()
        self.addCleanup(self.slack.close)
        self.hook = SlackWebhook(HOOK, base_url=self.slack.url.rsplit('/api', 1)[0])

    def test_posts_the_text_with_no_previews(self) -> None:
        self.slack.answer = (200, b'ok')
        self.hook.post_message('UNA-1 is done')
        [sent] = self.slack.requests
        self.assertEqual(sent['path'], '/services/T0001/B0002/sEcReTpart123')
        self.assertEqual(sent['body'], {'text': 'UNA-1 is done', 'unfurl_links': False, 'unfurl_media': False})
        self.assertEqual(sent['authorization'], '')  # the URL is the credential

    def test_a_refusal_is_said_as_what_to_do(self) -> None:
        for status, code, words in ((403, b'invalid_token', 'make a new one'), (404, b'no_service', 'it was removed'),
                                     (410, b'channel_is_archived', 'archived'), (429, b'', 'limiting'),
                                     (500, b'rollup_error', r'refused the message \(rollup_error\)'), (400, b'', r'\(400\)')):
            self.slack.answer = (status, code)
            with self.assertRaisesRegex(SlackError, words):
                self.hook.post_message('x')

    def test_an_unreachable_slack_never_repeats_the_secret(self) -> None:
        hook = SlackWebhook(HOOK)
        with mock.patch.object(hook.session, 'post', side_effect=OSError('Max retries exceeded with url: /services/T0001/B0002/sEcReTpart123')):
            with self.assertRaises(SlackError) as caught:
                hook.post_message('x')
        self.assertNotIn('sEcReTpart123', str(caught.exception))
        self.assertIn('could not reach Slack', str(caught.exception))

    def test_a_url_that_is_not_a_webhook_is_refused(self) -> None:
        with self.assertRaisesRegex(SlackError, 'not a Slack webhook URL'):
            SlackWebhook('https://acme.slack.com/archives/C0123456789')


if __name__ == '__main__':
    unittest.main()
