"""A finished task, announced in a Slack channel with its pull requests.

Two moments, ONE post per task (``SlackPostedTasks``):

* the operator presses **Done** (``TaskPublishService.finish_task_planning_session``
  — push, pull request, In Review): ``announce`` posts NOW and answers what
  happened, for the Done toast ("posted to Slack" / why not);
* the task reaches the tracker's Done (``DoneAnnouncements``: "Finish & forget"
  with done, a plan's "Count as done"): ``task_done`` — the task read NOW (the
  forget flow deletes its workspace next), posted in the background, unless
  Done already posted it.

Where it goes: an Incoming Webhook URL alone (the simplest), or a channel's link
plus the app's bot token. Best effort: a Slack that refuses or cannot be reached
is said in words that say what to do, and never fails the done.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from slack_core_lib.slack_core_lib.client.slack_client import SlackClient
from slack_core_lib.slack_core_lib.client.slack_webhook import SlackWebhook
from slack_core_lib.slack_core_lib.errors import SlackError
from slack_core_lib.slack_core_lib.helpers.channel_link import channel_from_link, webhook_url

from kato_core_lib.data_layers.data.release_note_source import ReleaseNoteSource
from kato_core_lib.data_layers.service.slack_posted_tasks import SlackPostedTasks
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.slack_done_settings_utils import SlackDoneSettings, slack_done_settings

POSTED = 'posted'
ALREADY_POSTED = 'already_posted'
NOT_SET_UP = 'not_set_up'
FAILED = 'failed'


def run_in_background(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name='slack-done', daemon=True).start()


def sender_for(settings: SlackDoneSettings, *, webhook_for=SlackWebhook, client_for=SlackClient):
    """``(send(text), '')``, or ``(None, what to fix)`` for a half set-up."""
    hook = webhook_url(settings.channel_link)
    if hook:
        return webhook_for(hook).post_message, ''
    channel = channel_from_link(settings.channel_link)
    if not channel:
        return None, ('the Slack link is neither a channel link nor an Incoming Webhook URL '
                      '(https://hooks.slack.com/services/…)')
    if not settings.token:
        return None, ('a channel link needs the Slack app\'s bot token (xoxb-…) — or paste the '
                      'channel\'s Incoming Webhook URL instead, which needs none')
    client = client_for(settings.token)
    return (lambda text: client.post_message(channel, text)), ''


class SlackDoneAnnouncer(object):
    def __init__(
        self,
        *,
        sources,
        links,
        posted: SlackPostedTasks | None = None,
        settings: Callable[[], SlackDoneSettings] = slack_done_settings,
        client_for: Callable[[str], SlackClient] = SlackClient,
        webhook_for: Callable[[str], SlackWebhook] = SlackWebhook,
        background: Callable[[Callable[[], None]], None] = run_in_background,
        logger=None,
    ) -> None:
        self._sources = sources
        self._links = links
        self._posted = posted or SlackPostedTasks()
        self._settings = settings
        self._client_for = client_for
        self._webhook_for = webhook_for
        self._background = background
        self.logger = logger or configure_logger('SlackDoneAnnouncer')

    def announce(self, task_id: str) -> dict:
        """Post ``task_id`` now (the Done button). ``{status, error}`` — status
        ``posted`` / ``already_posted`` / ``not_set_up`` / ``failed``. Never raises."""
        task = str(task_id or '').strip()
        try:
            prepared = self._prepare(task)
            if isinstance(prepared, dict):
                return prepared
            send, source = prepared
            return self._send(send, source)
        except Exception as exc:
            self.logger.exception('could not announce task %s in Slack', task)
            return _outcome(FAILED, str(exc) or exc.__class__.__name__)

    def task_done(self, task_id: str) -> None:
        """The task reached Done: posted in the background unless it already was."""
        task = str(task_id or '').strip()
        try:
            prepared = self._prepare(task)
            if isinstance(prepared, dict):
                return
            send, source = prepared
            self._background(lambda: self._send(send, source))
        except Exception:
            self.logger.exception('could not announce task %s in Slack', task)

    def _prepare(self, task: str):
        """``(send, source)`` read now — or the outcome that means "do not post"."""
        settings = self._settings()
        if not task or not settings.wanted:
            return _outcome(NOT_SET_UP)
        if self._posted.has(task):
            return _outcome(ALREADY_POSTED)
        send, problem = sender_for(settings, webhook_for=self._webhook_for, client_for=self._client_for)
        if problem:
            self.logger.warning('task %s was not posted to Slack: %s', task, problem)
            return _outcome(FAILED, problem)
        return send, self._sources.read(task)

    def _send(self, send, source: ReleaseNoteSource) -> dict:
        try:
            send(done_message(source, self._links.links(source)))
        except SlackError as exc:
            self.logger.error('task %s was not posted to Slack: %s', source.task_id, exc)
            return _outcome(FAILED, str(exc))
        except Exception as exc:
            self.logger.exception('could not post task %s to Slack', source.task_id)
            return _outcome(FAILED, str(exc) or exc.__class__.__name__)
        self._posted.mark(source.task_id)
        self.logger.info('posted task %s and its pull requests to Slack', source.task_id)
        return _outcome(POSTED)


def _outcome(status: str, error: str = '') -> dict:
    return {'status': status, 'error': error}


def done_message(source: ReleaseNoteSource, links: list[tuple[str, str]]) -> str:
    """``✅ *UNA-1* is done — Title``, then one line per pull request: its
    whole link, shown as the link itself (a word like "pull request" hid where
    it goes), after its repository's name when the task spans several.

    Everything that came from a ticket is escaped: a title holding
    ``<!channel>`` must never notify a whole channel.
    """
    title = _escape(source.title.strip())
    lines = [f'✅ *{_escape(source.task_id)}* is done' + (f' — {title}' if title else '')]
    if links:
        lines += [f'• {_where(label)}<{url}>' for label, url in links]
    else:
        lines.append('_No pull request was found for it._')
    return '\n'.join(lines)


def _where(label: str) -> str:
    """``backend: `` before the link — nothing for a task's only pull
    request (``PR``), whose link says it all."""
    return '' if label == 'PR' else f'{_escape(label)}: '


def _escape(text: str) -> str:
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
