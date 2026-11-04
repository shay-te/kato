"""Send a message through a Slack Incoming Webhook.

The simplest way to post: the webhook belongs to one channel, and its URL is
the whole credential — no app token, no channel id. That also makes the URL a
secret, so it is never repeated in an error. Slack answers ``200 ok``, or an
HTTP error with a short code; each a person can fix is said as what to do.
"""

from __future__ import annotations

from core_lib.client.client_base import ClientBase

from slack_core_lib.slack_core_lib.errors import SlackError
from slack_core_lib.slack_core_lib.helpers.channel_link import webhook_url

_MAKE_A_NEW_ONE = 'make a new one in the Slack app (Incoming Webhooks) and paste its URL'
_WHAT_TO_DO = {
    'invalid_token': f'the Slack webhook is not valid any more — {_MAKE_A_NEW_ONE}',
    'no_service': f'Slack has no such webhook (it was removed) — {_MAKE_A_NEW_ONE}',
    'no_team': f'the Slack webhook\'s workspace is gone — {_MAKE_A_NEW_ONE}',
    'team_disabled': 'the Slack workspace is disabled',
    'channel_not_found': f'the Slack webhook\'s channel no longer exists — {_MAKE_A_NEW_ONE}',
    'channel_is_archived': 'the Slack webhook\'s channel is archived',
    'action_prohibited': 'the Slack workspace does not let this webhook post',
    'posting_to_general_channel_denied': 'only Slack admins may post in the webhook\'s channel',
    'ratelimited': 'Slack is limiting how often this webhook posts — it will work again shortly',
}


class SlackWebhook(ClientBase):
    def __init__(self, url: str, *, base_url: str = '', timeout: int = 20) -> None:
        hook = webhook_url(url)
        if not hook:
            raise SlackError('that is not a Slack webhook URL — it starts with https://hooks.slack.com/services/')
        host, path = hook.split('/services/', 1)
        super().__init__(base_url or host)
        self.set_timeout(timeout)
        self._path = f'services/{path}'

    def post_message(self, text: str) -> None:
        """Post ``text`` (Slack's mrkdwn) to the webhook's channel, with no
        link previews: a list of links stays a list."""
        try:
            response = self._post(
                self._path, json={'text': text, 'unfurl_links': False, 'unfurl_media': False},
            )
        except OSError as exc:
            # The request's own error names the URL; the URL is the secret.
            raise SlackError(f'could not reach Slack: {str(exc).replace(self._path, "services/…")}') from exc
        if response.status_code == 200:
            return
        code = 'ratelimited' if response.status_code == 429 else (response.text or '').strip()[:80]
        raise SlackError(_WHAT_TO_DO.get(code) or f'Slack refused the message ({code or response.status_code})')
