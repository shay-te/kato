"""Send a message to a Slack channel as a Slack app (``chat.postMessage``).

The token is the app's bot token (``xoxb-…``) with the ``chat:write`` scope,
and the app must be in the channel. Slack answers HTTP 200 even when it refuses,
with ``{"ok": false, "error": "<code>"}``; each code a person can fix is said
as what to do.
"""

from __future__ import annotations

from core_lib.client.client_base import ClientBase

from slack_core_lib.slack_core_lib.errors import SlackError

SLACK_API_BASE_URL = 'https://slack.com/api'

_WHAT_TO_DO = {
    'invalid_auth': 'the Slack token is not valid — paste the app\'s bot token (xoxb-…) again',
    'not_authed': 'no Slack token was sent — set the app\'s bot token (xoxb-…)',
    'token_revoked': 'the Slack token was revoked — make a new one for the app',
    'account_inactive': 'the Slack app\'s account is deactivated',
    'channel_not_found': 'Slack has no such channel for this app — check the channel link, and for a '
                         'private channel invite the app first',
    'not_in_channel': 'the Slack app is not in the channel — invite it there (/invite @your-app)',
    'is_archived': 'the Slack channel is archived',
    'missing_scope': 'the Slack app may not post — give it the chat:write scope and reinstall it',
    'restricted_action': 'the Slack workspace does not let this app post in that channel',
    'ratelimited': 'Slack is limiting how often this app posts — it will work again shortly',
    'msg_too_long': 'the message is too long for Slack',
}


class SlackClient(ClientBase):
    def __init__(self, token: str, *, base_url: str = SLACK_API_BASE_URL, timeout: int = 20) -> None:
        super().__init__(base_url)
        self.set_timeout(timeout)
        self._token = str(token or '').strip()

    def post_message(self, channel: str, text: str) -> None:
        """Post ``text`` (Slack's mrkdwn) to ``channel`` (an id, or ``#name``).
        Links are not expanded into previews: a list of them stays a list."""
        if not self._token:
            raise SlackError(_WHAT_TO_DO['not_authed'])
        if not str(channel or '').strip():
            raise SlackError('no Slack channel was given — paste the channel\'s link')
        try:
            response = self._post(
                'chat.postMessage',
                json={'channel': channel, 'text': text, 'unfurl_links': False, 'unfurl_media': False},
                headers={'Authorization': f'Bearer {self._token}', 'Content-Type': 'application/json; charset=utf-8'},
            )
        except OSError as exc:
            raise SlackError(f'could not reach Slack: {exc}') from exc
        answer = _json(response)
        if response.status_code == 200 and answer.get('ok') is True:
            return
        code = str(answer.get('error') or '')
        if response.status_code == 429:
            code = 'ratelimited'
        raise SlackError(_WHAT_TO_DO.get(code) or f'Slack refused the message ({code or response.status_code})')


def _json(response) -> dict:
    try:
        answer = response.json()
    except ValueError:
        return {}
    return answer if isinstance(answer, dict) else {}
