"""Where finished tasks are announced in Slack: the channel's link and the token.

Read FRESH on every use (``live_setting``: settings.json, then the shell), so a
change in Settings applies to the next finished task without a restart. Both
empty = not set up: nothing is sent. The task's title and its pull request links
leave the machine, so it is the operator's choice to fill them in.
"""

from __future__ import annotations

from dataclasses import dataclass

from kato_core_lib.helpers.kato_settings_store_utils import live_setting

SLACK_DONE_CHANNEL_KEY = 'KATO_SLACK_DONE_CHANNEL'
SLACK_DONE_TOKEN_KEY = 'KATO_SLACK_BOT_TOKEN'
SLACK_DONE_KEYS = frozenset({SLACK_DONE_CHANNEL_KEY, SLACK_DONE_TOKEN_KEY})


@dataclass(frozen=True)
class SlackDoneSettings(object):
    channel_link: str = ''
    token: str = ''

    @property
    def wanted(self) -> bool:
        """The operator filled in either: set up (or half set up), not off."""
        return bool(self.channel_link or self.token)


def slack_done_settings(env: dict | None = None) -> SlackDoneSettings:
    return SlackDoneSettings(
        channel_link=live_setting(SLACK_DONE_CHANNEL_KEY, env),
        token=live_setting(SLACK_DONE_TOKEN_KEY, env),
    )
