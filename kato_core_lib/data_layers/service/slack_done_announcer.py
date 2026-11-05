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

**One post, even when both moments come at once.** Done on a plan's sub-task
moves its ticket to Done (which calls ``task_done``) and then calls ``announce``
for its toast: each checked "posted already?" before either had written it, and
the channel got the task twice. The right to post is now CLAIMED, under one lock,
before anything is sent; a second caller while that post is under way waits for
it and reports its outcome (the toast still says "posted"). A post that fails
gives the claim back, so the next Done tries again.
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
from kato_core_lib.helpers.agent_name_utils import agent_name
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.slack_done_settings_utils import SlackDoneSettings, slack_done_settings

POSTED = 'posted'
ALREADY_POSTED = 'already_posted'
NOT_SET_UP = 'not_set_up'
FAILED = 'failed'


def run_in_background(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name='slack-done', daemon=True).start()


class _Post(object):
    """A post of one task under way: whoever else asks waits for its outcome."""

    def __init__(self) -> None:
        self.finished = threading.Event()
        self.outcome: dict | None = None

#: How long a second caller waits for the post already under way.
WAIT_FOR_POST_SECONDS = 30


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
        signed_by: Callable[[], str] = agent_name,
        logger=None,
    ) -> None:
        self._sources = sources
        self._links = links
        self._posted = posted or SlackPostedTasks()
        self._settings = settings
        self._client_for = client_for
        self._webhook_for = webhook_for
        self._background = background
        self._signed_by = signed_by
        self.logger = logger or configure_logger('SlackDoneAnnouncer')
        self._claims_lock = threading.Lock()
        self._under_way: dict[str, _Post] = {}

    def announce(self, task_id: str) -> dict:
        """Post ``task_id`` now (the Done button). ``{status, error}`` — status
        ``posted`` / ``already_posted`` / ``not_set_up`` / ``failed``. Never raises."""
        task = str(task_id or '').strip()
        try:
            prepared = self._prepare(task)
            if isinstance(prepared, _Post):
                return self._outcome_of(prepared)
            if isinstance(prepared, dict):
                return prepared
            send, source, post = prepared
            return self._send(send, source, post)
        except Exception as exc:
            self.logger.exception('could not announce task %s in Slack', task)
            return _outcome(FAILED, str(exc) or exc.__class__.__name__)

    def task_done(self, task_id: str) -> None:
        """The task reached Done: posted in the background unless it already was."""
        task = str(task_id or '').strip()
        try:
            prepared = self._prepare(task)
            if isinstance(prepared, (dict, _Post)):
                return  # nothing to post, or a post of it is already under way
            send, source, post = prepared
            self._background(lambda: self._send(send, source, post))
        except Exception:
            self.logger.exception('could not announce task %s in Slack', task)

    def _prepare(self, task: str):
        """``(send, source, post)`` — this caller holds the claim to post —
        or the outcome that means "do not post", or the ``_Post`` already
        under way for the task."""
        settings = self._settings()
        if not task or not settings.wanted:
            return _outcome(NOT_SET_UP)
        send, problem = sender_for(settings, webhook_for=self._webhook_for, client_for=self._client_for)
        if problem:
            self.logger.warning('task %s was not posted to Slack: %s', task, problem)
            return _outcome(FAILED, problem)
        post = self._claim(task)
        if not isinstance(post, tuple):
            return post
        try:
            return send, self._sources.read(task), post[0]
        except Exception:
            self._finish(task, post[0], None)  # gives the claim back
            raise

    def _claim(self, task: str):
        """``(post,)``: this caller may post ``task``. Else ALREADY_POSTED,
        or the ``_Post`` another caller is sending right now. Checked and
        taken under one lock — that is what makes the post happen once."""
        key = task.lower()
        with self._claims_lock:
            under_way = self._under_way.get(key)
            if under_way is not None:
                return under_way
            if self._posted.has(task):
                return _outcome(ALREADY_POSTED)
            post = _Post()
            self._under_way[key] = post
            return (post,)

    def _send(self, send, source: ReleaseNoteSource, post: _Post) -> dict:
        outcome = None
        try:
            send(done_message(source, self._links.links(source), self._signed_by()))
        except SlackError as exc:
            self.logger.error('task %s was not posted to Slack: %s', source.task_id, exc)
            outcome = _outcome(FAILED, str(exc))
        except Exception as exc:
            self.logger.exception('could not post task %s to Slack', source.task_id)
            outcome = _outcome(FAILED, str(exc) or exc.__class__.__name__)
        else:
            self._posted.mark(source.task_id)
            self.logger.info('posted task %s and its pull requests to Slack', source.task_id)
            outcome = _outcome(POSTED)
        finally:
            self._finish(source.task_id, post, outcome)
        return outcome

    def _finish(self, task: str, post: _Post, outcome: dict | None) -> None:
        """The post is over: its claim is given back (a failure retries on the
        next Done) and whoever waited is told how it went."""
        with self._claims_lock:
            if self._under_way.get(task.lower()) is post:
                del self._under_way[task.lower()]
        post.outcome = outcome
        post.finished.set()

    def _outcome_of(self, post: _Post) -> dict:
        """What the post already under way came to — the toast says that, not
        "already posted" for a post it just watched happen."""
        if not post.finished.wait(WAIT_FOR_POST_SECONDS):
            return _outcome(ALREADY_POSTED)  # still sending: it is one post either way
        return post.outcome or _outcome(FAILED, 'the post did not finish')


def _outcome(status: str, error: str = '') -> dict:
    return {'status': status, 'error': error}


def done_message(source: ReleaseNoteSource, links: list[tuple[str, str]], signed_by: str = '') -> str:
    """``✅ *UNA-1* is done — Title``, then whose agent finished it (several
    developers' katos post to one channel), then one line per pull request:
    its whole link, shown as the link itself (a word like "pull request" hid
    where it goes), after its repository's name when the task spans several.

    Everything that came from a ticket or a setting is escaped: a title
    holding ``<!channel>`` must never notify a whole channel.
    """
    title = _escape(source.title.strip())
    lines = [f'✅ *{_escape(source.task_id)}* is done' + (f' — {title}' if title else '')]
    if signed_by.strip():
        lines.append(f'Finished by *{_escape(signed_by.strip())}*')
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
