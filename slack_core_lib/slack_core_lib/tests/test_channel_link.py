import unittest

from slack_core_lib.slack_core_lib.helpers.channel_link import channel_from_link


class ChannelLinkTests(unittest.TestCase):
    def test_the_links_slack_gives(self) -> None:
        cases = {
            'https://acme.slack.com/archives/C0123ABCD': 'C0123ABCD',
            '  https://acme.slack.com/archives/C0123ABCD/p1700000000000100  ': 'C0123ABCD',
            'https://acme.slack.com/archives/G01PRIVATE9?thread=x': 'G01PRIVATE9',
            'https://app.slack.com/client/T0TEAM123/C0123ABCD': 'C0123ABCD',
            'https://app.slack.com/client/T0TEAM123/C0123ABCD/thread/x': 'C0123ABCD',
            'C0123ABCD': 'C0123ABCD',
            '#Releases': '#releases',
            '#team-done_2': '#team-done_2',
        }
        for text, channel in cases.items():
            self.assertEqual(channel_from_link(text), channel, text)

    def test_anything_else_is_no_channel(self) -> None:
        for text in ('', None, '   ', 'releases', 'https://acme.slack.com/archives/', 'https://example.com/C0123',
                     'c0123abcd', '#', '#with space', 'https://acme.slack.com/archives/X0123ABCD'):
            self.assertEqual(channel_from_link(text), '', text)


if __name__ == '__main__':
    unittest.main()
