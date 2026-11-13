"""Done posts a task to Slack again once something was committed in it since.

Operator's report: "I clicked Done, it said already posted to Slack" — after the
agent had changed files since the last post. A task is now posted once PER
COMMITTED STATE: each post keeps the commit every repository was on, and a
later Done whose repositories moved on posts again, marked as updated.

Real git clones in a real WorkspaceService, the real ``TaskCommits`` reading
them through a real ``GitClientMixin``, the real Slack client talking HTTP to
a local stand-in for Slack. Only the pull request lookup is a stub.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from git_core_lib.git_core_lib.client.git_client import GitClientMixin
from slack_core_lib.slack_core_lib.client.slack_client import SlackClient
from slack_core_lib.slack_core_lib.tests.fakes import FakeSlack

from kato_core_lib.data_layers.service.release_note_sources import ReleaseNoteSources
from kato_core_lib.data_layers.service.slack_done_announcer import (
    COMMITS_UNREADABLE,
    SlackDoneAnnouncer,
    done_message,
    moved_on_since,
)
from kato_core_lib.data_layers.service.slack_posted_tasks import SlackPost, SlackPostedTasks
from kato_core_lib.data_layers.service.task_commits import TaskCommits
from kato_core_lib.data_layers.data.release_note_source import ReleaseNoteSource
from kato_core_lib.helpers.slack_done_settings_utils import SlackDoneSettings
from tests.chaos_lib import build_real_workspace_service

CHANNEL = 'https://acme.slack.com/archives/C0RELEASE1'


class _Git(GitClientMixin):
    def __init__(self) -> None:
        self.logger = logging.getLogger('test.slack_posts_again')


def _git(cwd: Path, *args: str, at: int | None = None) -> str:
    env = None
    if at is not None:
        env = dict(os.environ, GIT_COMMITTER_DATE=f'@{at} +0000', GIT_AUTHOR_DATE=f'@{at} +0000')
    return subprocess.run(
        ['git', *args], cwd=str(cwd), check=True, capture_output=True, text=True, env=env,
    ).stdout.strip()


class _Links(object):
    def links(self, _source):
        return [('backend', 'https://git.example/backend/pull/7')]


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='slack-again-', ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.workspaces = build_real_workspace_service(self.root / 'workspaces')
        self.workspaces.create(task_id='UNA-1', task_summary='Fix the login bug',
                               repository_ids=['backend', 'client'])
        self.clones = {
            repo: self._clone(self.workspaces.repository_path('UNA-1', repo))
            for repo in ('backend', 'client')
        }
        self.slack = FakeSlack()
        self.addCleanup(self.slack.close)
        self.posted = SlackPostedTasks(self.root / 'slack_posted.json', clock=lambda: self.now)
        self.now = 1_700_000_500.0
        self.commits = TaskCommits(workspace_manager=self.workspaces, git=_Git())
        self.announcer = SlackDoneAnnouncer(
            sources=ReleaseNoteSources(workspace_manager=self.workspaces, task_service=MagicMock()),
            links=_Links(),
            posted=self.posted,
            settings=lambda: SlackDoneSettings(channel_link=CHANNEL, token='xoxb-1'),
            client_for=lambda token: SlackClient(token, base_url=self.slack.url),
            background=lambda work: work(),
            signed_by=lambda: "Shay Tessler's kato",
            commits=self.commits.read,
            logger=MagicMock(),
        )

    @staticmethod
    def _clone(path: Path) -> Path:
        path.mkdir(parents=True)
        _git(path, 'init', '-q', '-b', 'UNA-1')
        _git(path, 'config', 'user.email', 't@example.com')
        _git(path, 'config', 'user.name', 'test')
        (path / 'readme.txt').write_text('start\n')
        _git(path, 'add', 'readme.txt')
        _git(path, 'commit', '-q', '-m', 'start', at=1_700_000_000)
        return path

    def _commit(self, repo: str, *, at: int = 1_700_000_900) -> None:
        clone = self.clones[repo]
        (clone / 'change.txt').write_text(f'changed at {at}\n')
        _git(clone, 'add', 'change.txt')
        _git(clone, 'commit', '-q', '-m', 'more work', at=at)

    def _texts(self) -> list[str]:
        return [request['body'].get('text', '') for request in self.slack.requests]


class PostsAgainWhenTheWorkMovedOnTests(_Base):
    def test_nothing_committed_since_is_already_posted(self) -> None:
        self.assertEqual(self.announcer.announce('UNA-1')['status'], 'posted')
        self.assertEqual(self.announcer.announce('UNA-1'), {'status': 'already_posted', 'error': ''})
        self.assertEqual(len(self.slack.requests), 1)

    def test_a_commit_since_the_last_post_posts_it_again_marked_as_updated(self) -> None:
        self.announcer.announce('UNA-1')
        self._commit('client')
        self.assertEqual(self.announcer.announce('UNA-1'), {'status': 'posted_again', 'error': ''})
        self.assertEqual(len(self.slack.requests), 2)
        first, second = self._texts()
        self.assertNotIn('Updated', first)
        self.assertIn('_Updated: it changed since it was last posted here._', second)
        # …and that new state is the one kept: pressing Done again says so.
        self.assertEqual(self.announcer.announce('UNA-1')['status'], 'already_posted')
        self.assertEqual(len(self.slack.requests), 2)

    def test_an_edit_that_is_not_committed_does_not_post_it_again(self) -> None:
        self.announcer.announce('UNA-1')
        (self.clones['backend'] / 'readme.txt').write_text('edited, never committed\n')
        self.assertEqual(self.announcer.announce('UNA-1')['status'], 'already_posted')
        self.assertEqual(len(self.slack.requests), 1)

    def test_the_tracker_s_done_posts_it_again_too_once_it_changed(self) -> None:
        self.announcer.announce('UNA-1')
        self.announcer.task_done('UNA-1')  # nothing new: not posted
        self.assertEqual(len(self.slack.requests), 1)
        self._commit('backend')
        self.announcer.task_done('UNA-1')
        self.assertEqual(len(self.slack.requests), 2)

    def test_commits_it_cannot_read_never_post_twice_and_say_why(self) -> None:
        self.announcer.announce('UNA-1')
        self._commit('client')
        _git(self.clones['client'], 'checkout', '-q', '--orphan', 'empty')  # HEAD has no commit
        self.assertEqual(self.announcer.announce('UNA-1'),
                         {'status': 'already_posted', 'error': COMMITS_UNREADABLE})
        self.assertEqual(len(self.slack.requests), 1)

    def test_each_post_keeps_where_every_repository_stood(self) -> None:
        self.announcer.announce('UNA-1')
        saved = json.loads((self.root / 'slack_posted.json').read_text())['una-1']
        self.assertEqual(saved['at'], self.now)
        self.assertEqual(saved['commits'], {
            repo: _git(clone, 'rev-parse', 'HEAD') for repo, clone in self.clones.items()
        })

    def test_a_failed_repost_keeps_the_old_post_so_the_next_done_tries_again(self) -> None:
        self.announcer.announce('UNA-1')
        self._commit('client')
        self.slack.answer = (200, {'ok': False, 'error': 'channel_not_found'})
        self.assertEqual(self.announcer.announce('UNA-1')['status'], 'failed')
        self.slack.answer = (200, {'ok': True, 'ts': '1.3'})
        self.assertEqual(self.announcer.announce('UNA-1')['status'], 'posted_again')


class PostsKeptBeforeCommitsWereRecordedTests(_Base):
    """A post written before this change is a bare time: a commit made after it
    counts as moved on, one made before it does not."""

    def _legacy_post(self, at: float) -> None:
        (self.root / 'slack_posted.json').write_text(json.dumps({'una-1': at}))

    def test_nothing_committed_after_it_is_already_posted(self) -> None:
        self._legacy_post(1_700_000_100.0)  # the clones' commits are older
        self.assertEqual(self.announcer.announce('UNA-1'), {'status': 'already_posted', 'error': ''})

    def test_a_commit_after_it_posts_again_and_from_then_on_commits_are_kept(self) -> None:
        self._legacy_post(1_700_000_100.0)
        self._commit('backend', at=1_700_000_200)
        self.assertEqual(self.announcer.announce('UNA-1')['status'], 'posted_again')
        self.assertIn('commits', json.loads((self.root / 'slack_posted.json').read_text())['una-1'])
        self.assertEqual(self.announcer.announce('UNA-1')['status'], 'already_posted')


class MovedOnSinceTests(unittest.TestCase):
    def test_cannot_tell_is_never_moved_on(self) -> None:
        self.assertFalse(moved_on_since(SlackPost(at=1.0, commits={'a': 'x'}), None))
        self.assertFalse(moved_on_since(SlackPost(at=1.0), None))

    def test_kept_commits_are_compared_repository_by_repository(self) -> None:
        post = SlackPost(at=1.0, commits={'a': 'x', 'b': 'y'})
        self.assertFalse(moved_on_since(post, {'a': ('x', 9), 'b': ('y', 9)}))
        self.assertTrue(moved_on_since(post, {'a': ('x', 9), 'b': ('z', 9)}))
        self.assertTrue(moved_on_since(post, {'a': ('x', 9), 'b': ('y', 9), 'c': ('w', 9)}))

    def test_an_older_post_is_compared_by_time(self) -> None:
        post = SlackPost(at=100.0)
        self.assertFalse(moved_on_since(post, {'a': ('x', 100), 'b': ('y', 50)}))
        self.assertTrue(moved_on_since(post, {'a': ('x', 101)}))


class SlackPostedTasksFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'posted.json'

    def test_commits_round_trip_and_a_post_without_them_keeps_none(self) -> None:
        store = SlackPostedTasks(self.path, clock=lambda: 5.0)
        store.mark(' UNA-1 ', {'backend': 'abc'})
        store.mark('UNA-2')
        self.assertEqual(store.last_post('una-1'), SlackPost(at=5.0, commits={'backend': 'abc'}))
        self.assertEqual(store.last_post('UNA-2'), SlackPost(at=5.0))
        self.assertIsNone(store.last_post('UNA-3'))

    def test_old_and_unreadable_entries(self) -> None:
        self.path.write_text(json.dumps({
            'old': 12.5, 'flag': True, 'text': 'x', 'bad-at': {'at': 'soon', 'commits': {'a': 'b'}},
            'bad-commits': {'at': 3, 'commits': ['a']},
        }))
        store = SlackPostedTasks(self.path)
        self.assertEqual(store.last_post('old'), SlackPost(at=12.5))
        self.assertIsNone(store.last_post('flag'))
        self.assertIsNone(store.last_post('text'))
        self.assertEqual(store.last_post('bad-at'), SlackPost(at=0.0, commits={'a': 'b'}))
        self.assertEqual(store.last_post('bad-commits'), SlackPost(at=3.0))


class TaskCommitsTests(_Base):
    def test_each_repository_s_last_commit(self) -> None:
        reading = self.commits.read('UNA-1')
        self.assertEqual(set(reading), {'backend', 'client'})
        self.assertEqual(reading['backend'], (_git(self.clones['backend'], 'rev-parse', 'HEAD'), 1_700_000_000))

    def test_anything_it_cannot_read_is_none(self) -> None:
        self.assertIsNone(self.commits.read(''))
        self.assertIsNone(self.commits.read('UNA-404'))  # no workspace
        self.assertIsNone(TaskCommits(workspace_manager=None, git=_Git()).read('UNA-1'))
        self.assertIsNone(TaskCommits(workspace_manager=self.workspaces, git=None).read('UNA-1'))
        self.workspaces.create(task_id='UNA-2', task_summary='no repositories', repository_ids=[])
        self.assertIsNone(self.commits.read('UNA-2'))
        broken = MagicMock()
        broken.last_commit.side_effect = RuntimeError('git is gone')
        self.assertIsNone(TaskCommits(workspace_manager=self.workspaces, git=broken).read('UNA-1'))

    def test_a_repository_with_no_clone_on_disk_is_none(self) -> None:
        import shutil
        shutil.rmtree(self.clones['client'] / '.git')
        self.assertIsNone(self.commits.read('UNA-1'))


class DoneMessageTests(unittest.TestCase):
    def test_only_a_repost_says_it_was_updated(self) -> None:
        source = ReleaseNoteSource(task_id='UNA-1', title='Fix it')
        self.assertNotIn('Updated', done_message(source, [], 'kato'))
        self.assertEqual(
            done_message(source, [], 'kato', again=True).splitlines()[:3],
            ['✅ *UNA-1* is done — Fix it', 'Finished by *kato*',
             '_Updated: it changed since it was last posted here._'],
        )


if __name__ == '__main__':
    unittest.main()
