"""A finished task, announced in a Slack channel with its pull requests.

Told by ``TaskStateService._announce_done`` (through ``DoneAnnouncements``) —
the one place a task reaching Done is announced: "Finish & forget" with done,
and a plan's "Count as done". The task is read NOW, because the forget flow
deletes its workspace next (and with it a branch the operator named); its pull
requests are then found by that branch, and the message sent, in the
background — the same reading and the same links as the release notes.

Best effort: a Slack that refuses or cannot be reached is logged, in words that
say what to do, and never fails the done.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from slack_core_lib.slack_core_lib.client.slack_client import SlackClient
from slack_core_lib.slack_core_lib.errors import SlackError
from slack_core_lib.slack_core_lib.helpers.channel_link import channel_from_link

from kato_core_lib.data_layers.data.release_note_source import ReleaseNoteSource
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.slack_done_settings_utils import SlackDoneSettings, slack_done_settings


def run_in_background(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name='slack-done', daemon=True).start()


class SlackDoneAnnouncer(object):
    def __init__(
        self,
        *,
        sources,
        links,
        settings: Callable[[], SlackDoneSettings] = slack_done_settings,
        client_for: Callable[[str], SlackClient] = SlackClient,
        background: Callable[[Callable[[], None]], None] = run_in_background,
        logger=None,
    ) -> None:
        self._sources = sources
        self._links = links
        self._settings = settings
        self._client_for = client_for
        self._background = background
        self.logger = logger or configure_logger('SlackDoneAnnouncer')

    def task_done(self, task_id: str) -> None:
        """Announce ``task_id`` (Done) in the configured channel. Never raises."""
        task = str(task_id or '').strip()
        if not task:
            return
        try:
            settings = self._settings()
            if not settings.wanted:
                return
            channel = channel_from_link(settings.channel_link)
            if not channel or not settings.token:
                self.logger.warning(
                    'task %s is done, but it was not posted to Slack: %s', task,
                    'the Slack token is missing' if channel else 'the Slack channel link is not a channel link',
                )
                return
            source = self._sources.read(task)
            self._background(lambda: self._post(source, channel, settings.token))
        except Exception:
            self.logger.exception('could not announce task %s in Slack', task)

    def _post(self, source: ReleaseNoteSource, channel: str, token: str) -> None:
        try:
            text = done_message(source, self._links.links(source))
            self._client_for(token).post_message(channel, text)
        except SlackError as exc:
            self.logger.error('task %s is done, but it was not posted to Slack: %s', source.task_id, exc)
        except Exception:
            self.logger.exception('could not post task %s to Slack', source.task_id)
        else:
            self.logger.info('posted task %s and its pull requests to Slack', source.task_id)


def done_message(source: ReleaseNoteSource, links: list[tuple[str, str]]) -> str:
    """``✅ *UNA-1* is done — Title``, then one line per pull request.

    Everything that came from a ticket is escaped: a title holding
    ``<!channel>`` must never notify a whole channel.
    """
    title = _escape(source.title.strip())
    lines = [f'✅ *{_escape(source.task_id)}* is done' + (f' — {title}' if title else '')]
    if links:
        lines += [f'• <{url}|{_escape(_label(label))}>' for label, url in links]
    else:
        lines.append('_No pull request was found for it._')
    return '\n'.join(lines)


def _label(label: str) -> str:
    return 'pull request' if label == 'PR' else label


def _escape(text: str) -> str:
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
