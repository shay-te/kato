"""Kato never reports work as delivered when it did not reach the remote.

Operator report: "if kato is not able to push anything or not able to update
source or not able to write commits to the bitbucket or github, notify me there
itself ... don't show me a updated source green box for this, it is very
misleading".

What was wrong, each pinned here:

- Update source went on after a failed push, so a source could read "updated"
  without the work ever reaching it;
- git refusing to switch the source, with nothing changed, counted as updated;
- a push skipped for a real reason (a clone off its task branch) was not a
  problem at all, so Done moved the ticket to In Review with nothing pushed.

Real git and a real remote that really refuses, through a pre-receive hook, or
over HTTP the way a hosted service does.
"""

from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.test_local_task_branch_name import BRANCH, RealLocalTask, _git


class HonestGitResultsTests(RealLocalTask, unittest.TestCase):
    def _refuse_every_push(self) -> None:
        hook = self.remote / 'hooks' / 'pre-receive'
        hook.write_text('#!/bin/sh\necho "refused by the host" >&2\nexit 1\n', encoding='utf-8')
        hook.chmod(0o755)

    def _task_with_work(self) -> tuple[str, Path]:
        task_id, clone = self._prepared()
        self._commit_work(clone)
        return task_id, clone

    def _source(self) -> Path:
        return self.source_root / 'api'

    def _source_branch(self) -> str:
        return _git(self._source(), 'rev-parse', '--abbrev-ref', 'HEAD')

    def _completion_line(self) -> str:
        return next(
            call.args[0] % call.args[1:]
            for call in self.publisher.logger.info.call_args_list
            if 'source update finished' in str(call.args[0])
        )

    # ----- the host refuses the push -----

    def test_a_refused_push_says_what_the_host_said(self) -> None:
        self._refuse_every_push()
        task_id, _clone = self._task_with_work()

        result = self.publisher.push_task(task_id)

        self.assertEqual(result['pushed_repositories'], [])
        [failure] = result['failed_repositories']
        self.assertEqual(failure['repository_id'], 'api')
        self.assertIn('refused by the host', failure['error'])

    def test_update_source_does_not_call_a_refused_push_updated(self) -> None:
        self._refuse_every_push()
        task_id, _clone = self._task_with_work()

        result = self.publisher.update_source_for_task(task_id)

        self.assertFalse(result['updated'])
        self.assertEqual(result['updated_repositories'], [])
        [failure] = result['failed_repositories']
        self.assertIn('did not reach the remote', failure['error'])
        self.assertIn('refused by the host', failure['error'])
        self.assertIn('0 updated, 0 skipped, 1 failed', self._completion_line())

    def test_a_refused_push_leaves_the_source_alone(self) -> None:
        self._refuse_every_push()
        task_id, _clone = self._task_with_work()

        self.publisher.update_source_for_task(task_id)

        self.assertEqual(self._source_branch(), 'master')

    def test_done_does_not_move_the_ticket_when_nothing_reached_the_remote(self) -> None:
        self._refuse_every_push()
        task_id, _clone = self._task_with_work()

        result = self.publisher.finish_task_planning_session(task_id)

        self.assertFalse(result['finished'])
        self.task_state.move_task_to_review.assert_not_called()

    # ----- the work is somewhere a push cannot take it from -----

    def _clone_off_its_branch(self) -> str:
        task_id, clone = self._prepared()
        _git(clone, 'checkout', '-q', 'master')
        self._commit_work(clone)  # its own commit, so kato will not move it
        return task_id

    def test_a_clone_off_its_task_branch_is_a_problem_not_a_harmless_skip(self) -> None:
        task_id = self._clone_off_its_branch()

        result = self.publisher.push_task(task_id)

        [skipped] = result['skipped_repositories']
        self.assertTrue(skipped['problem'], skipped)
        self.assertIn('not the task branch', skipped['reason'])

    def test_update_source_fails_for_a_clone_off_its_task_branch(self) -> None:
        task_id = self._clone_off_its_branch()

        result = self.publisher.update_source_for_task(task_id)

        self.assertEqual(result['updated_repositories'], [])
        self.assertIn('not the task branch', result['failed_repositories'][0]['error'])

    def test_done_stops_for_a_clone_off_its_task_branch(self) -> None:
        task_id = self._clone_off_its_branch()

        result = self.publisher.finish_task_planning_session(task_id)

        self.assertFalse(result['finished'])
        self.task_state.move_task_to_review.assert_not_called()

    # ----- git refuses to switch the source -----

    def test_a_switch_git_refused_is_not_called_updated(self) -> None:
        task_id, clone = self._prepared()
        (clone / 'app.py').write_text('VALUE = 2\n', encoding='utf-8')
        _git(clone, 'config', 'user.email', 't@example.com')
        _git(clone, 'config', 'user.name', 'test')
        _git(clone, 'add', '-A')
        _git(clone, 'commit', '-qm', 'change app')
        (self._source() / 'app.py').write_text('VALUE = 99  # mine\n', encoding='utf-8')

        result = self.publisher.update_source_for_task(task_id)

        self.assertFalse(result['updated'])
        self.assertEqual(result['updated_repositories'], [])
        self.assertTrue(result['warnings'][0]['blocked'], result['warnings'])
        self.assertEqual(self._source_branch(), 'master')
        self.assertIn('0 updated, 0 skipped, 1 failed', self._completion_line())

    # ----- and what is genuinely fine still reads as fine -----

    def test_work_that_is_on_the_remote_is_updated_as_before(self) -> None:
        task_id, _clone = self._task_with_work()

        result = self.publisher.update_source_for_task(task_id)

        self.assertTrue(result['updated'])
        self.assertEqual(result['updated_repositories'], ['api'])
        self.assertEqual(result['failed_repositories'], [])
        self.assertEqual(self._source_branch(), BRANCH)

    def test_already_on_the_remote_is_no_problem(self) -> None:
        task_id, _clone = self._task_with_work()
        self.publisher.push_task(task_id)

        result = self.publisher.update_source_for_task(task_id)

        [skipped] = result['pushed']['skipped_repositories']
        self.assertFalse(skipped['problem'], skipped)
        self.assertIn('nothing to push', skipped['reason'])
        self.assertEqual(result['updated_repositories'], ['api'])


class _RefusingHost(BaseHTTPRequestHandler):
    """What a host answers a push it refuses, before any pack is sent."""

    def do_GET(self) -> None:
        self.server.authorizations.append(self.headers.get('Authorization', ''))
        body = self.server.message.encode('utf-8')
        self.send_response(self.server.status)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(body)))
        if self.server.status == 401:
            self.send_header('WWW-Authenticate', 'Basic realm="Git"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


class HostRefusesOverHttpTests(RealLocalTask, unittest.TestCase):
    """Real git talking to a server over HTTP, through the real push path.

    The refusal happens where a hosted service refuses a login, which a
    pre-receive hook never reaches. The wording for a missing login is what
    github.com and bitbucket.org really produce.
    """

    TOKEN = 'the-secret-token'

    def _serve(self, status: int, message: str = '') -> str:
        server = ThreadingHTTPServer(('127.0.0.1', 0), _RefusingHost)
        server.status, server.message, server.authorizations = status, message, []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.server = server
        return f'http://127.0.0.1:{server.server_address[1]}/acme/api.git'

    def _push_to(self, url: str) -> dict:
        task_id, clone = self._prepared()
        self._commit_work(clone)
        _git(clone, 'remote', 'set-url', 'origin', url)
        repository = self.repositories.get_repository('api')
        repository.remote_url = url
        repository.token = self.TOKEN
        return self.publisher.push_task(task_id)

    def test_a_host_that_refuses_write_access_is_heard_in_its_own_words(self) -> None:
        result = self._push_to(self._serve(403, 'Permission to acme/api.git denied to bot.\n'))

        self.assertEqual(result['pushed_repositories'], [])
        [failure] = result['failed_repositories']
        self.assertIn('Permission to acme/api.git denied to bot', failure['error'])
        self.assertIn('403', failure['error'])

    def test_no_login_fails_at_once_and_never_waits_for_a_prompt(self) -> None:
        result = self._push_to(self._serve(401))

        self.assertEqual(result['pushed_repositories'], [])
        self.assertIn('terminal prompts disabled', result['failed_repositories'][0]['error'])

    def test_the_token_never_reaches_what_the_operator_is_shown(self) -> None:
        result = self._push_to(self._serve(403, 'denied\n'))

        self.assertNotIn(self.TOKEN, str(result))

    def test_update_source_stays_away_from_a_push_the_host_refused(self) -> None:
        url = self._serve(403, 'Permission to acme/api.git denied to bot.\n')
        task_id, clone = self._prepared()
        self._commit_work(clone)
        _git(clone, 'remote', 'set-url', 'origin', url)
        self.repositories.get_repository('api').remote_url = url

        result = self.publisher.update_source_for_task(task_id)

        self.assertEqual(result['updated_repositories'], [])
        self.assertIn('did not reach the remote', result['failed_repositories'][0]['error'])
        self.assertEqual(_git(self.source_root / 'api', 'rev-parse', '--abbrev-ref', 'HEAD'), 'master')


if __name__ == '__main__':
    unittest.main()
