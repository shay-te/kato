"""End-to-end coverage for the "Update source" planning-UI button.

Pins down the contract: clicking ``Update source`` runs pure git
plumbing (no AI involvement) that:

  1. Pushes the per-task workspace clone's branch to origin.
  2. For each repository the task touches, locates the inventory
     clone under ``REPOSITORY_ROOT_PATH`` and switches it to the
     task branch via ``fetch`` / ``checkout`` / ``pull --ff-only``.
  3. Refuses to update a source clone that has uncommitted changes
     (operator's running system — never silently overwrite).

The tests below exercise both layers:

* ``RepositoryService.update_source_to_task_branch`` — the per-repo
  git operations against a real on-disk repository (origin + local
  clone, both initialized via ``git init``).
* ``TaskPublishService.update_source_for_task`` — the orchestration that
  iterates the task's repositories and aggregates per-repo results.
* ``POST /api/sessions/<task_id>/update-source`` — the Flask route
  that wraps the agent-service method.

Real-git tests live alongside mock-based ones because the failure
modes the operator cares about (dirty source, missing branch, fast-
forward refused) are git-state-dependent and can't be captured by
mocks alone.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kato_core_lib.data_layers.service.repository_service import RepositoryService
from tests.utils import build_test_cfg


def _git(cwd: Path, *args: str) -> None:
    """Run a git command in ``cwd``, raising on non-zero exit."""
    subprocess.run(
        ['git', '-C', str(cwd), *args],
        check=True, capture_output=True, text=True,
    )


def _make_origin_with_branch(origin: Path, branch: str = 'master') -> None:
    """Initialize a bare-ish origin repo with one commit on ``branch``."""
    origin.mkdir(parents=True, exist_ok=True)
    _git(origin, 'init', '-b', branch)
    _git(origin, 'config', 'user.email', 'kato-test@example.com')
    _git(origin, 'config', 'user.name', 'kato-test')
    (origin / 'README.md').write_text('initial\n', encoding='utf-8')
    _git(origin, 'add', '-A')
    _git(origin, 'commit', '-m', 'initial')


def _clone(origin: Path, dest: Path) -> None:
    """Clone ``origin`` into ``dest`` and configure a test identity."""
    subprocess.run(
        ['git', 'clone', str(origin), str(dest)],
        check=True, capture_output=True, text=True,
    )
    _git(dest, 'config', 'user.email', 'kato-test@example.com')
    _git(dest, 'config', 'user.name', 'kato-test')


def _push_branch_to_origin(work_clone: Path, branch: str) -> None:
    """Create a branch with one commit on ``work_clone`` and push it.

    Simulates the kato per-task workspace pushing its branch before
    the source-update flow runs.
    """
    _git(work_clone, 'checkout', '-b', branch)
    (work_clone / 'feature.txt').write_text('hello\n', encoding='utf-8')
    _git(work_clone, 'add', '-A')
    _git(work_clone, 'commit', '-m', f'feat: {branch}')
    _git(work_clone, 'push', '-u', 'origin', branch)


def _current_branch(cwd: Path) -> str:
    out = subprocess.run(
        ['git', '-C', str(cwd), 'rev-parse', '--abbrev-ref', 'HEAD'],
        check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


class _RepoStub:
    """Minimal stand-in for the inventory ``Repository`` object."""

    def __init__(self, repo_id: str, local_path: Path) -> None:
        self.id = repo_id
        self.local_path = str(local_path)


# ----- repository service: per-repo git operations ----------------------------


class UpdateSourceToTaskBranchTests(unittest.TestCase):
    """``RepositoryService.update_source_to_task_branch`` against real git."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.origin = self.tmp / 'origin'
        self.source = self.tmp / 'source'
        self.work = self.tmp / 'workspace'
        # Set up: origin with master + a task branch already pushed
        # (simulates push_task having run).
        _make_origin_with_branch(self.origin, 'master')
        _clone(self.origin, self.work)
        _push_branch_to_origin(self.work, 'UNA-1234')
        # The "source" clone the operator runs locally — clone of origin,
        # initially on master, knows nothing about UNA-1234 yet.
        _clone(self.origin, self.source)
        self.service = RepositoryService(build_test_cfg(), 3)

    def test_switches_source_clone_to_task_branch_and_fast_forwards(self) -> None:
        repo = _RepoStub('myrepo', self.source)

        self.service.update_source_to_task_branch(repo, 'UNA-1234')

        self.assertEqual(_current_branch(self.source), 'UNA-1234')
        # The task-branch commit is now reachable from HEAD.
        self.assertTrue((self.source / 'feature.txt').is_file())

    def test_idempotent_when_already_on_task_branch_and_up_to_date(self) -> None:
        repo = _RepoStub('myrepo', self.source)
        self.service.update_source_to_task_branch(repo, 'UNA-1234')
        # Second call: should be a no-op (fetch is no-op, checkout
        # no-op, pull says "Already up to date.").
        self.service.update_source_to_task_branch(repo, 'UNA-1234')

        self.assertEqual(_current_branch(self.source), 'UNA-1234')

    def test_local_changes_are_CARRIED_not_stashed(self) -> None:
        # The operator's source folder is a RUNNING system. The old
        # stash → switch → pull → pop round trip rewrote their working
        # files twice: a local dev config reverted for the duration of the
        # switch and came back with a new mtime, so a running dev server
        # picked up the committed config mid-switch and had to be restarted
        # by hand on EVERY branch switch. Preserving the content is not
        # enough when the file itself is what a process watches.
        (self.source / 'unfinished.txt').write_text('wip\n', encoding='utf-8')
        _git(self.source, 'add', 'unfinished.txt')
        repo = _RepoStub('myrepo', self.source)

        result = self.service.update_source_to_task_branch(repo, 'UNA-1234')

        self.assertEqual(_current_branch(self.source), 'UNA-1234')
        self.assertEqual(
            (self.source / 'unfinished.txt').read_text(encoding='utf-8'), 'wip\n',
        )
        self.assertTrue(result['updated'])
        self.assertFalse(result['blocked'])
        self.assertTrue(result['carried_changes'])
        self.assertIn('carried across', result['warning'])

    def test_nothing_is_ever_put_in_the_stash(self) -> None:
        # The load-bearing assertion: not "the changes survived" but "the
        # working file was never touched", which is what the dev server
        # actually cares about.
        (self.source / 'unfinished.txt').write_text('wip\n', encoding='utf-8')
        _git(self.source, 'add', 'unfinished.txt')
        repo = _RepoStub('myrepo', self.source)

        self.service.update_source_to_task_branch(repo, 'UNA-1234')

        stash_list = subprocess.run(
            ['git', 'stash', 'list'], cwd=self.source,
            capture_output=True, text=True, check=False,
        ).stdout.strip()
        self.assertEqual(stash_list, '', 'kato must not create a stash')

    def test_untracked_files_are_left_alone(self) -> None:
        (self.source / 'scratch.log').write_text('debug\n', encoding='utf-8')
        repo = _RepoStub('myrepo', self.source)

        result = self.service.update_source_to_task_branch(repo, 'UNA-1234')

        self.assertEqual(_current_branch(self.source), 'UNA-1234')
        self.assertEqual(
            (self.source / 'scratch.log').read_text(encoding='utf-8'), 'debug\n',
        )
        self.assertTrue(result['updated'])

    def test_a_genuinely_conflicting_change_STOPS_and_reports(self) -> None:
        # The operator edited a file the task branch also changes. Git
        # refuses the switch, and that refusal is the whole point: it is
        # rare, it means the branches really do disagree about a file the
        # operator has open, and it is their call. Kato names the file and
        # changes nothing.
        (self.source / 'feature.txt').write_text(
            'local-only different content\n', encoding='utf-8',
        )
        repo = _RepoStub('myrepo', self.source)

        result = self.service.update_source_to_task_branch(repo, 'UNA-1234')

        self.assertFalse(result['updated'])
        self.assertTrue(result['blocked'])
        self.assertIn('feature.txt', result['blocking_paths'])
        self.assertIn('would be overwritten', result['warning'].lower())

    def test_a_blocked_switch_leaves_the_branch_and_the_file_untouched(self) -> None:
        # "Nothing was stashed or changed" has to be literally true, or the
        # operator cannot trust the message and has to go check by hand.
        (self.source / 'feature.txt').write_text(
            'local-only different content\n', encoding='utf-8',
        )
        repo = _RepoStub('myrepo', self.source)
        before_branch = _current_branch(self.source)

        self.service.update_source_to_task_branch(repo, 'UNA-1234')

        self.assertEqual(_current_branch(self.source), before_branch)
        self.assertEqual(
            (self.source / 'feature.txt').read_text(encoding='utf-8'),
            'local-only different content\n',
        )
        stash_list = subprocess.run(
            ['git', 'stash', 'list'], cwd=self.source,
            capture_output=True, text=True, check=False,
        ).stdout.strip()
        self.assertEqual(stash_list, '')

    def test_raises_on_missing_local_path(self) -> None:
        repo = _RepoStub('myrepo', Path(''))
        repo.local_path = ''
        with self.assertRaises(RuntimeError) as cm:
            self.service.update_source_to_task_branch(repo, 'UNA-1234')
        self.assertIn('no local_path', str(cm.exception))

    def test_raises_on_non_git_directory(self) -> None:
        not_a_repo = self.tmp / 'plain-folder'
        not_a_repo.mkdir()
        repo = _RepoStub('myrepo', not_a_repo)

        with self.assertRaises(RuntimeError) as cm:
            self.service.update_source_to_task_branch(repo, 'UNA-1234')

        self.assertIn('not a git repository', str(cm.exception))

    def test_raises_when_branch_does_not_exist_anywhere(self) -> None:
        # Branch was never pushed — fetch is fine, but checkout
        # of a non-existent branch fails. Surface as RuntimeError.
        repo = _RepoStub('myrepo', self.source)

        with self.assertRaises(RuntimeError) as cm:
            self.service.update_source_to_task_branch(repo, 'UNA-DOES-NOT-EXIST')

        self.assertIn('checkout', str(cm.exception).lower())
        # Source stayed on its original branch.
        self.assertEqual(_current_branch(self.source), 'master')

    def test_refuses_fast_forward_when_source_has_diverged(self) -> None:
        # Operator already had the task branch locally with their
        # OWN commit on top — divergent from origin. ``pull --ff-only``
        # refuses, so kato refuses too instead of silently merging.
        _git(self.source, 'fetch', 'origin', '--prune')
        _git(self.source, 'checkout', 'UNA-1234')
        # Mutate the source's history off-line.
        (self.source / 'local-only.txt').write_text('mine\n', encoding='utf-8')
        _git(self.source, 'add', '-A')
        _git(self.source, 'commit', '-m', 'local divergence')
        # Now push a new commit to origin's UNA-1234 from the work clone.
        (self.work / 'remote-only.txt').write_text('theirs\n', encoding='utf-8')
        _git(self.work, 'add', '-A')
        _git(self.work, 'commit', '-m', 'remote divergence')
        _git(self.work, 'push', 'origin', 'UNA-1234')

        repo = _RepoStub('myrepo', self.source)
        with self.assertRaises(RuntimeError) as cm:
            self.service.update_source_to_task_branch(repo, 'UNA-1234')

        # The exact git wording varies by version, but the kato wrapper
        # message names the operation that failed.
        self.assertIn('fast-forward', str(cm.exception).lower())


# ----- repository service: workspace_has_task_changes ------------------------


class WorkspaceHasTaskChangesTests(unittest.TestCase):
    """``RepositoryService.workspace_has_task_changes`` against real git.

    Pins the "Update source" skip rule: the source clone is only
    touched when the workspace clone actually carries task-branch
    commits. Repos the agent never modified are left alone.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.origin = self.tmp / 'origin'
        self.workspace = self.tmp / 'workspace'
        _make_origin_with_branch(self.origin, 'master')
        _clone(self.origin, self.workspace)
        self.service = RepositoryService(build_test_cfg(), 3)

    def test_skips_when_workspace_still_on_master(self) -> None:
        # Agent never created the task branch — workspace is on master.
        # Nothing to propagate to the source folder.
        repo = _RepoStub('myrepo', self.workspace)

        self.assertFalse(
            self.service.workspace_has_task_changes(repo, 'UNA-1234'),
        )

    def test_skips_when_on_task_branch_but_zero_commits_ahead(self) -> None:
        # Agent created the branch but made no commits. Switching the
        # source folder would be busywork — and the branch may not even
        # exist on origin yet, so the checkout would fail.
        _git(self.workspace, 'checkout', '-b', 'UNA-1234')
        repo = _RepoStub('myrepo', self.workspace)

        self.assertFalse(
            self.service.workspace_has_task_changes(repo, 'UNA-1234'),
        )

    def test_skips_when_on_task_branch_with_untracked_files_only(self) -> None:
        # Untracked artifacts (test runs, build outputs) must NOT count
        # as "changes". Only commits on the task branch do — otherwise
        # every repo the agent ran tests in looks "changed" and the
        # operator's source folder gets yanked off its current branch
        # for nothing.
        _git(self.workspace, 'checkout', '-b', 'UNA-1234')
        (self.workspace / 'test-output.log').write_text(
            'pytest noise\n', encoding='utf-8',
        )
        (self.workspace / '.pytest_cache').mkdir()
        (self.workspace / '.pytest_cache' / 'v').write_text(
            'cache', encoding='utf-8',
        )
        repo = _RepoStub('myrepo', self.workspace)

        self.assertFalse(
            self.service.workspace_has_task_changes(repo, 'UNA-1234'),
        )

    def test_skips_when_on_task_branch_with_modified_tracked_files_uncommitted(
        self,
    ) -> None:
        # Even uncommitted edits to TRACKED files don't count —
        # ``update_source_to_task_branch`` only does fetch/checkout/pull,
        # so uncommitted changes never propagate via the branch anyway.
        _git(self.workspace, 'checkout', '-b', 'UNA-1234')
        (self.workspace / 'README.md').write_text(
            'modified but not committed\n', encoding='utf-8',
        )
        repo = _RepoStub('myrepo', self.workspace)

        self.assertFalse(
            self.service.workspace_has_task_changes(repo, 'UNA-1234'),
        )

    def test_updates_when_task_branch_has_a_commit_ahead_of_master(self) -> None:
        # Agent committed work on the task branch. THIS is the case
        # the source folder needs to be updated for.
        _git(self.workspace, 'checkout', '-b', 'UNA-1234')
        (self.workspace / 'feature.py').write_text(
            'def new_feature(): pass\n', encoding='utf-8',
        )
        _git(self.workspace, 'add', '-A')
        _git(self.workspace, 'commit', '-m', 'feat: add new_feature')
        repo = _RepoStub('myrepo', self.workspace)

        self.assertTrue(
            self.service.workspace_has_task_changes(repo, 'UNA-1234'),
        )

    def test_safe_default_true_on_missing_local_path(self) -> None:
        repo = _RepoStub('myrepo', Path(''))
        repo.local_path = ''
        # Empty local_path is an unexpected state. Returning True lets
        # the update path run + raise its own clearer error rather than
        # silently swallowing work the operator was expecting.
        self.assertTrue(
            self.service.workspace_has_task_changes(repo, 'UNA-1234'),
        )

    def test_safe_default_true_on_non_git_directory(self) -> None:
        not_a_repo = self.tmp / 'plain-folder'
        not_a_repo.mkdir()
        repo = _RepoStub('myrepo', not_a_repo)
        self.assertTrue(
            self.service.workspace_has_task_changes(repo, 'UNA-1234'),
        )

    def test_safe_default_true_on_empty_branch_name(self) -> None:
        repo = _RepoStub('myrepo', self.workspace)
        self.assertTrue(
            self.service.workspace_has_task_changes(repo, ''),
        )


# ----- agent service: orchestration ------------------------------------------


class UpdateSourceForTaskOrchestrationTests(unittest.TestCase):
    """``TaskPublishService.update_source_for_task`` aggregates per-repo results.

    Mocks the underlying ``RepositoryService`` to focus on the
    orchestration logic — push first, then iterate inventory repos,
    skip-on-missing, surface failures in the response.
    """

    def setUp(self) -> None:
        # Build a minimal AgentService stub by patching __init__ so we
        # don't drag in 14 collaborator services. Every test sets the
        # attributes it needs and exercises one method.
        from kato_core_lib.data_layers.service.agent_service import AgentService
        from kato_core_lib.data_layers.service.task_publish_service import (
            TaskPublishService,
        )

        # Bypass __init__ — we only test methods, not construction.
        # Build the publish service directly. It used to be a half-built
        # AgentService via ``__new__`` with the collaborators poked in; now
        # that publishing owns its object, real construction says exactly
        # what this code depends on — which is the point of the split.
        self._workspace_manager = MagicMock()
        self._repository_service = MagicMock()
        self._task_service = MagicMock()
        self.agent = TaskPublishService(
            repository_service=self._repository_service,
            task_service=self._task_service,
            task_state_service=MagicMock(),
            task_publisher=MagicMock(),
            workspace_manager=self._workspace_manager,
            logger=MagicMock(),
        )

        # Workspace returns two repo IDs for our test task.
        wm = self._workspace_manager
        wm.get.return_value = types.SimpleNamespace(
            task_id='UNA-1234',
            task_summary='Fix the thing',
            repository_ids=['client', 'backend'],
        )
        wm.repository_path.side_effect = lambda task_id, repo_id: Path(
            f'/fake/workspaces/{task_id}/{repo_id}',
        )

        # Each call to get_repository returns a stub with a distinct
        # source-side ``local_path`` so we can tell which repo the
        # update was attempted for.
        rs = self._repository_service
        rs.get_repository.side_effect = lambda repo_id: types.SimpleNamespace(
            id=repo_id,
            local_path=f'/fake/source/{repo_id}',
        )
        rs.build_branch_name.side_effect = lambda task, repo: task.id

    def test_returns_error_when_task_id_is_blank(self) -> None:
        result = self.agent.update_source_for_task('')

        self.assertFalse(result['updated'])
        self.assertIn('empty task id', result.get('error', ''))

    def test_returns_no_workspace_when_workspace_missing(self) -> None:
        self._workspace_manager.get.return_value = None

        # push_task short-circuits on missing workspace too.
        with patch.object(self.agent, 'push_task') as push_mock:
            push_mock.return_value = {
                'pushed': False,
                'error': 'no workspace context for this task',
            }
            with patch.object(
                self.agent, '_resolve_publish_context',
                return_value=([], '', None),
            ):
                result = self.agent.update_source_for_task('UNA-1234')

        self.assertFalse(result['updated'])
        self.assertIn('no workspace', result['error'])

    def test_pushes_then_updates_each_source_repo(self) -> None:
        # Fake the push step + the source-update step independently.
        push_payload = {
            'pushed': True,
            'pushed_repositories': ['client', 'backend'],
            'failed_repositories': [],
        }
        with patch.object(self.agent, 'push_task', return_value=push_payload) as push_mock:
            result = self.agent.update_source_for_task('UNA-1234')

        push_mock.assert_called_once_with('UNA-1234')
        # update_source_to_task_branch invoked once per inventory repo
        # with the task-id branch.
        calls = self._repository_service.update_source_to_task_branch.call_args_list
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(call.kwargs.get('branch_name', call.args[1]), 'UNA-1234')

        self.assertTrue(result['updated'])
        self.assertEqual(set(result['updated_repositories']), {'client', 'backend'})
        self.assertEqual(result['failed_repositories'], [])

    def test_logs_one_completion_line_for_the_ui_notification(self) -> None:
        # A single "Mission <task>: source update finished (...)" line is
        # root-logged so the planning UI's status feed can fire the "Source
        # updated" OS notification when the operator is on another window.
        with patch.object(self.agent, 'push_task', return_value={'pushed': True}):
            self.agent.update_source_for_task('UNA-1234')
        completion = [
            c for c in self.agent.logger.info.call_args_list
            if c.args and 'source update finished' in str(c.args[0])
        ]
        self.assertEqual(len(completion), 1)
        fmt, *args = completion[0].args
        self.assertEqual(
            fmt % tuple(args),
            'Mission UNA-1234: source update finished '
            '(2 updated, 0 skipped, 0 failed)',
        )

    def test_partial_failure_reports_per_repo(self) -> None:
        # Source-update succeeds for client, fails for backend.
        rs = self._repository_service

        def _fake_update(repo, branch):
            if repo.id == 'backend':
                raise RuntimeError(
                    f'source folder for {repo.id} at {repo.local_path} has '
                    'uncommitted changes',
                )

        rs.update_source_to_task_branch.side_effect = _fake_update
        with patch.object(self.agent, 'push_task', return_value={'pushed': True}):
            result = self.agent.update_source_for_task('UNA-1234')

        self.assertEqual(result['updated_repositories'], ['client'])
        self.assertEqual(len(result['failed_repositories']), 1)
        self.assertEqual(result['failed_repositories'][0]['repository_id'], 'backend')
        self.assertIn('uncommitted', result['failed_repositories'][0]['error'])
        # ``updated=True`` because at least one repo succeeded —
        # operator gets a partial-success toast, not a hard failure.
        self.assertTrue(result['updated'])

    def test_skips_repo_when_inventory_entry_has_no_local_path(self) -> None:
        # Inventory entry exists but ``local_path`` is empty (operator
        # configured kato without REPOSITORY_ROOT_PATH, or the entry
        # was added manually without a path). Skip with a clear
        # reason rather than crashing on the empty path.
        rs = self._repository_service

        def _lookup(repo_id):
            return types.SimpleNamespace(
                id=repo_id,
                local_path='' if repo_id == 'backend' else f'/fake/source/{repo_id}',
            )
        rs.get_repository.side_effect = _lookup
        with patch.object(self.agent, 'push_task', return_value={'pushed': True}):
            result = self.agent.update_source_for_task('UNA-1234')

        self.assertEqual(result['updated_repositories'], ['client'])
        self.assertEqual(len(result['skipped_repositories']), 1)
        self.assertEqual(result['skipped_repositories'][0]['repository_id'], 'backend')
        self.assertIn('local_path', result['skipped_repositories'][0]['reason'])


# ----- Flask endpoint --------------------------------------------------------


class UpdateSourceEndpointTests(unittest.TestCase):
    """``POST /api/sessions/<task_id>/update-source`` wraps the service."""

    def _client(self, agent_service):
        from kato_webserver.app import create_app

        app = create_app(
            session_manager=None,
            workspace_manager=None,
            planning_session_runner=None,
        )
        app.config['AGENT_SERVICE'] = agent_service
        return app.test_client()

    def test_returns_503_when_agent_service_missing(self) -> None:
        client = self._client(agent_service=None)

        response = client.post('/api/sessions/UNA-1234/update-source')

        self.assertEqual(response.status_code, 503)
        self.assertIn('not wired', response.get_json().get('error', ''))

    def test_returns_501_when_agent_service_lacks_method(self) -> None:
        agent = types.SimpleNamespace()  # no update_source_for_task
        client = self._client(agent_service=agent)

        response = client.post('/api/sessions/UNA-1234/update-source')

        self.assertEqual(response.status_code, 501)

    def test_returns_payload_when_update_succeeds(self) -> None:
        agent = MagicMock()
        agent.publish.update_source_for_task.return_value = {
            'updated': True,
            'task_id': 'UNA-1234',
            'updated_repositories': ['client', 'backend'],
            'failed_repositories': [],
            'skipped_repositories': [],
        }
        client = self._client(agent)

        response = client.post('/api/sessions/UNA-1234/update-source')

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body['updated'])
        self.assertEqual(body['updated_repositories'], ['client', 'backend'])
        agent.publish.update_source_for_task.assert_called_once_with('UNA-1234')

    def test_returns_404_when_no_workspace_context(self) -> None:
        agent = MagicMock()
        agent.publish.update_source_for_task.return_value = {
            'updated': False,
            'task_id': 'UNA-1234',
            'error': 'no workspace context for this task',
        }
        client = self._client(agent)

        response = client.post('/api/sessions/UNA-1234/update-source')

        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
