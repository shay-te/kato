"""End-to-end proof for the three symptoms reported on task pickup.

REAL git repositories on disk, real clones, the real ``RepositoryService``.
No mocked git — the earlier round of tests for this area passed while the
shipped code destroyed work, because they stubbed the very seam that
contained the bug.

The reported symptoms, verbatim:

1. "when kato gets a new task from the youtrack he will clone all the repos
   but will not create the branch by the task name in them, all the repos
   will sit on master and he will just delete the entire code from some
   repos"
2. "when I manually add a repo to the task from Add repository button it
   adds the repos and does the same thing, keeps it on master and deletes
   all the code inside the repo"
3. "after pulling the repo in both cases he will create the branch in my
   working directory repos"

Each test below asserts the three things that must ALL hold after pickup:

* the WORKSPACE clone is on the task branch
* the WORKSPACE clone has the source files in it
* the operator's SOURCE checkout is untouched — still on its own branch,
  and no task branch was created in it
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from kato_core_lib.data_layers.service.repository_service import RepositoryService
from kato_core_lib.data_layers.service.workspace_provisioning_service import (
    provision_task_workspace_clones,
)

TASK_BRANCH = 'UNA-3025'


def _git(cwd, *args):
    subprocess.run(
        ['git', *args], cwd=str(cwd), check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _branches(repo: Path) -> set[str]:
    out = subprocess.run(
        ['git', 'branch', '--format=%(refname:short)'], cwd=str(repo),
        capture_output=True, text=True,
    ).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def _current_branch(repo: Path) -> str:
    return subprocess.run(
        ['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=str(repo),
        capture_output=True, text=True,
    ).stdout.strip()


class _Service(RepositoryService):
    """The REAL service, really constructed.

    Only the logger is replaced, and only to keep test output quiet — every
    git call, the inventory, and the branch machinery are the production
    ones. The destination branch is not stubbed either: the real
    ``destination_branch`` reads it off the repository, so the fixtures set
    it there like the inventory would.
    """

    def __init__(self):
        super().__init__([], 3)
        self.logger = SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None,
            exception=lambda *a, **k: None, error=lambda *a, **k: None,
            debug=lambda *a, **k: None,
        )


class _Workspace:
    """Minimal stand-in for WorkspaceManager over a real directory."""

    def __init__(self, root: Path):
        self.root = root
        self.status: list[str] = []

    def repository_path(self, task_id, repository_id):
        return self.root / str(task_id) / str(repository_id)

    def create(self, **kwargs):
        (self.root / str(kwargs.get('task_id'))).mkdir(parents=True, exist_ok=True)

    def append_preflight_log(self, task_id, message):
        return None

    def update_status(self, task_id, status):
        self.status.append(status)

    # The sync path asks the workspace which repos it already holds.
    def get(self, task_id):
        task_dir = self.root / str(task_id)
        if not task_dir.is_dir():
            return None
        ids = sorted(d.name for d in task_dir.iterdir() if (d / '.git').is_dir())
        return SimpleNamespace(repository_ids=ids)


class TaskPickupEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        # The REMOTE the repo is cloned from.
        self.remote = self.root / 'remote' / 'form-core-lib'
        self.remote.mkdir(parents=True)
        _git(self.remote, 'init', '-q', '-b', 'master')
        _git(self.remote, 'config', 'user.email', 't@example.com')
        _git(self.remote, 'config', 'user.name', 'test')
        (self.remote / 'form_service.py').write_text('PAGES = 1\n', encoding='utf-8')
        (self.remote / 'README.md').write_text('# form\n', encoding='utf-8')
        _git(self.remote, 'add', '-A')
        _git(self.remote, 'commit', '-qm', 'initial')

        # The OPERATOR'S source checkout — "dev-una". Must stay untouched.
        self.source_root = self.root / 'dev-una'
        self.source_root.mkdir()
        _git(self.source_root, 'clone', '-q', str(self.remote), 'form-core-lib')
        self.source = self.source_root / 'form-core-lib'

        # Where kato's per-task clones go — "dev-kato".
        self.workspaces = self.root / 'dev-kato'
        self.workspaces.mkdir()
        self.workspace = _Workspace(self.workspaces)

        self.service = _Service()
        self.repository = SimpleNamespace(
            id='form-core-lib',
            local_path=str(self.source),
            remote_url=str(self.remote),
            destination_branch='master',
        )
        self.env = mock.patch.dict(os.environ, {
            'REPOSITORY_ROOT_PATH': str(self.source_root),
            'KATO_WORKSPACES_ROOT': str(self.workspaces),
        })

    def _pick_up_task(self):
        """Exactly what pickup does: provision clones, then prepare branches."""
        provisioned = provision_task_workspace_clones(
            self.workspace,
            self.service,
            SimpleNamespace(id=TASK_BRANCH, summary='s', description='d'),
            [self.repository],
        )
        self.service.prepare_task_branches(
            provisioned, {r.id: TASK_BRANCH for r in provisioned},
        )
        return provisioned

    # -- symptom 1 --------------------------------------------------------

    def test_the_workspace_clone_ends_up_ON_THE_TASK_BRANCH(self) -> None:
        with self.env:
            provisioned = self._pick_up_task()
        clone = Path(provisioned[0].local_path)
        self.assertEqual(
            _current_branch(clone), TASK_BRANCH,
            'the workspace clone was left on the default branch',
        )

    def test_the_workspace_clone_still_HAS_THE_CODE(self) -> None:
        # "he will just delete the entire code from some repos"
        with self.env:
            provisioned = self._pick_up_task()
        clone = Path(provisioned[0].local_path)
        self.assertTrue(
            (clone / 'form_service.py').is_file(),
            'the workspace clone has no source files in it',
        )
        self.assertEqual(
            (clone / 'form_service.py').read_text(encoding='utf-8'), 'PAGES = 1\n',
        )

    def test_the_clone_is_under_the_workspaces_root_not_the_source_root(self) -> None:
        with self.env:
            provisioned = self._pick_up_task()
        clone = Path(provisioned[0].local_path).resolve()
        self.assertTrue(clone.is_relative_to(self.workspaces.resolve()))
        self.assertFalse(clone.is_relative_to(self.source_root.resolve()))

    # -- symptom 3 --------------------------------------------------------

    def test_the_operators_SOURCE_checkout_gets_no_task_branch(self) -> None:
        # "he will create the branch in my working directory repos"
        before = _branches(self.source)
        with self.env:
            self._pick_up_task()
        after = _branches(self.source)
        self.assertEqual(
            after, before,
            f'a branch was created in the operator source checkout: {after - before}',
        )
        self.assertNotIn(TASK_BRANCH, after)

    def test_the_operators_SOURCE_checkout_stays_on_its_own_branch(self) -> None:
        with self.env:
            self._pick_up_task()
        self.assertEqual(_current_branch(self.source), 'master')

    def test_the_operators_SOURCE_files_are_untouched(self) -> None:
        # The source tree is a running system. Nothing about picking up a
        # task may rewrite a file in it.
        (self.source / 'form_service.py').write_text(
            'LOCAL EDIT\n', encoding='utf-8',
        )
        with self.env:
            self._pick_up_task()
        self.assertEqual(
            (self.source / 'form_service.py').read_text(encoding='utf-8'),
            'LOCAL EDIT\n',
            'picking up a task rewrote a file in the operator source tree',
        )

    # -- the guard, proven against real paths -----------------------------

    def test_branch_prep_REFUSES_a_source_tree_repository(self) -> None:
        # If anything ever hands branch prep the inventory object again, it
        # must fail loudly rather than branch the operator's checkout.
        with self.env, self.assertRaises(RuntimeError) as caught:
            self.service.prepare_task_branches(
                [self.repository], {'form-core-lib': TASK_BRANCH},
            )
        self.assertIn('source tree', str(caught.exception))
        self.assertNotIn(TASK_BRANCH, _branches(self.source))

    # -- symptom 2: the "Add repository" path ------------------------------

    def test_a_repo_added_MID_TASK_lands_on_the_task_branch_with_code(self) -> None:
        # The manual "+ Add repository" flow reaches the same two calls, but
        # for a task whose workspace already exists.
        with self.env:
            self._pick_up_task()  # first repo already there
            second_remote = self.root / 'remote' / 'event-core-lib'
            second_remote.mkdir(parents=True)
            _git(second_remote, 'init', '-q', '-b', 'master')
            _git(second_remote, 'config', 'user.email', 't@example.com')
            _git(second_remote, 'config', 'user.name', 'test')
            (second_remote / 'events.py').write_text('E = 2\n', encoding='utf-8')
            _git(second_remote, 'add', '-A')
            _git(second_remote, 'commit', '-qm', 'initial')
            _git(self.source_root, 'clone', '-q', str(second_remote), 'event-core-lib')

            added = SimpleNamespace(
                id='event-core-lib',
                local_path=str(self.source_root / 'event-core-lib'),
                remote_url=str(second_remote),
                destination_branch='master',
            )
            provisioned = provision_task_workspace_clones(
                self.workspace,
                self.service,
                SimpleNamespace(id=TASK_BRANCH, summary='s', description='d'),
                [self.repository, added],
            )
            newly = [r for r in provisioned if r.id == 'event-core-lib']
            self.service.prepare_task_branches(
                newly, {r.id: TASK_BRANCH for r in newly},
            )

        clone = Path(newly[0].local_path)
        self.assertEqual(_current_branch(clone), TASK_BRANCH)
        self.assertTrue(
            (clone / 'events.py').is_file(),
            'the mid-task added repo has no code in it',
        )
        self.assertNotIn(
            TASK_BRANCH, _branches(self.source_root / 'event-core-lib'),
            'the mid-task add branched the operator source checkout',
        )


class AdversarialPickupTests(TaskPickupEndToEndTests):
    """Pickup under the conditions that actually differ on the operator's box.

    The happy path was already proven. These are the states that route
    pickup into ``_make_git_ready_for_work`` — the checkout -f / reset
    --hard / clean -fd path — or into a branch base that is not what the
    caller assumed.
    """

    def test_a_clone_that_reports_DIRTY_immediately_keeps_its_code(self) -> None:
        # The Windows case: with core.autocrlf misconfigured a freshly
        # cloned repo reports every file as modified, so branch prep takes
        # the dirty branch on the very first pickup.
        with self.env:
            provisioned = provision_task_workspace_clones(
                self.workspace,
                self.service,
                SimpleNamespace(id=TASK_BRANCH, summary='s', description='d'),
                [self.repository],
            )
            clone = Path(provisioned[0].local_path)
            # Make it dirty before branch prep runs, as autocrlf would.
            (clone / 'form_service.py').write_text('PAGES = 1\r\n', encoding='utf-8')
            self.service.prepare_task_branches(
                provisioned, {r.id: TASK_BRANCH for r in provisioned},
            )
        self.assertEqual(_current_branch(clone), TASK_BRANCH)
        self.assertTrue(
            (clone / 'form_service.py').is_file(),
            'a dirty-on-arrival clone lost its code during branch prep',
        )
        self.assertTrue((clone / 'README.md').is_file())

    def test_a_task_branch_that_already_exists_on_the_remote_is_reused(self) -> None:
        # Second pickup of the same ticket, or a branch pushed earlier.
        _git(self.remote, 'branch', TASK_BRANCH)
        with self.env:
            provisioned = self._pick_up_task()
        clone = Path(provisioned[0].local_path)
        self.assertEqual(_current_branch(clone), TASK_BRANCH)
        self.assertTrue((clone / 'form_service.py').is_file())

    def test_picking_the_same_task_up_TWICE_is_stable(self) -> None:
        # The scan loop re-runs pickup; the second pass must not undo the
        # first or empty the clone it already prepared.
        with self.env:
            self._pick_up_task()
            provisioned = self._pick_up_task()
        clone = Path(provisioned[0].local_path)
        self.assertEqual(_current_branch(clone), TASK_BRANCH)
        self.assertTrue((clone / 'form_service.py').is_file())
        self.assertNotIn(TASK_BRANCH, _branches(self.source))

    def test_a_STRANDED_clone_with_work_does_not_lose_it_on_pickup(self) -> None:
        # The second route into the wipe. The idempotence guard only covers
        # a clone ALREADY on its task branch; one stranded on the default
        # branch — a prep that failed, a run that died — that then collected
        # the agent's output still went through checkout -f / reset --hard /
        # clean -fd. Same symptom, different path in.
        clone = self.workspaces / TASK_BRANCH / 'form-core-lib'
        clone.parent.mkdir(parents=True, exist_ok=True)
        _git(clone.parent, 'clone', '-q', str(self.remote), 'form-core-lib')
        self.assertEqual(_current_branch(clone), 'master')
        (clone / 'form_service.py').write_text('AGENT WORK\n', encoding='utf-8')
        (clone / 'new_file.py').write_text('brand new\n', encoding='utf-8')

        stranded = SimpleNamespace(
            id='form-core-lib', local_path=str(clone),
            remote_url=str(self.remote), destination_branch='master',
        )
        with self.env:
            self.service.prepare_task_branches(
                [stranded], {'form-core-lib': TASK_BRANCH},
            )

        self.assertEqual(_current_branch(clone), TASK_BRANCH)
        stashes = subprocess.run(
            ['git', 'stash', 'list'], cwd=str(clone),
            capture_output=True, text=True,
        ).stdout
        self.assertIn(
            'form-core-lib', stashes,
            'a stranded clone was wiped with no way back to the work',
        )
        _git(clone, 'stash', 'apply', 'stash@{0}')
        self.assertEqual(
            (clone / 'form_service.py').read_text(encoding='utf-8'), 'AGENT WORK\n',
        )
        self.assertTrue(
            (clone / 'new_file.py').is_file(),
            'untracked files the agent created were not preserved',
        )

    def test_a_reused_branch_still_clears_stale_BUILD_OUTPUT(self) -> None:
        # The other side of the idempotence guard. Returning early must not
        # cost the existing cleanup: generated artifacts left over from a
        # previous run should still go when that is ALL that is dirty.
        with self.env:
            provisioned = self._pick_up_task()
            clone = Path(provisioned[0].local_path)
            build = clone / 'build'
            build.mkdir()
            (build / 'main.js').write_text('compiled\n', encoding='utf-8')
            self._pick_up_task()
        self.assertFalse(build.exists(), 'stale build output survived a re-pickup')

    def test_build_output_is_NOT_cleared_when_real_work_sits_beside_it(self) -> None:
        # The distinction that makes the cleanup safe: the moment a source
        # edit is mixed in, nothing is discarded at all.
        with self.env:
            provisioned = self._pick_up_task()
            clone = Path(provisioned[0].local_path)
            build = clone / 'build'
            build.mkdir()
            (build / 'main.js').write_text('compiled\n', encoding='utf-8')
            (clone / 'form_service.py').write_text('AGENT WORK\n', encoding='utf-8')
            self._pick_up_task()
        self.assertEqual(
            (clone / 'form_service.py').read_text(encoding='utf-8'), 'AGENT WORK\n',
        )

    def test_agent_work_survives_a_SECOND_pickup_pass(self) -> None:
        # The scan tick must not wipe work the agent already did.
        with self.env:
            provisioned = self._pick_up_task()
            clone = Path(provisioned[0].local_path)
            (clone / 'form_service.py').write_text('AGENT WORK\n', encoding='utf-8')
            self._pick_up_task()
        self.assertEqual(
            (clone / 'form_service.py').read_text(encoding='utf-8'), 'AGENT WORK\n',
            'a second pickup pass destroyed the agent work in progress',
        )


class FailedTaskMustNotDestroyAgentWorkTests(TaskPickupEndToEndTests):
    """A task FAILING must not erase what the agent wrote.

    ``handle_task_failure`` calls ``restore_task_repositories(force=True)``,
    which on a dirty tree ran ``checkout -f`` → ``reset --hard`` →
    ``clean -fd`` with no safety net. Every repo the agent had actually
    modified ended up back on the destination branch and empty — which is
    the reported symptom exactly, including why it was only SOME repos: a
    repo the agent never touched is clean and returns early.
    """

    def _worked_on_task(self):
        """Pick the task up, then write what the agent would have written."""
        provisioned = self._pick_up_task()
        clone = Path(provisioned[0].local_path)
        (clone / 'form_service.py').write_text('PAGES = 99\n', encoding='utf-8')
        (clone / 'brand_new.py').write_text('added by agent\n', encoding='utf-8')
        return provisioned, clone

    def test_a_forced_restore_does_not_LOSE_the_agents_work(self) -> None:
        with self.env:
            provisioned, clone = self._worked_on_task()
            self.service.restore_task_repositories(provisioned, force=True)
            stashes = subprocess.run(
                ['git', 'stash', 'list'], cwd=str(clone),
                capture_output=True, text=True,
            ).stdout
            self.assertIn(
                'form-core-lib', stashes,
                'the forced restore discarded the work with no stash',
            )
            # And it is really recoverable, not just listed.
            _git(clone, 'stash', 'apply', 'stash@{0}')
        self.assertEqual(
            (clone / 'form_service.py').read_text(encoding='utf-8'), 'PAGES = 99\n',
        )
        self.assertTrue(
            (clone / 'brand_new.py').is_file(),
            'untracked files the agent created were not preserved',
        )

    def test_untracked_files_are_stashed_too(self) -> None:
        # ``clean -fd`` is part of the restore, so a stash without -u would
        # preserve modified files and delete brand-new ones.
        with self.env:
            provisioned, clone = self._worked_on_task()
            self.service.restore_task_repositories(provisioned, force=True)
            self.assertFalse((clone / 'brand_new.py').is_file())
            _git(clone, 'stash', 'apply', 'stash@{0}')
        self.assertTrue((clone / 'brand_new.py').is_file())

    def test_the_restore_ABORTS_when_the_work_cannot_be_stashed(self) -> None:
        # Fail-closed: if the parking step does not take, nothing
        # destructive may run. A repo left on the task branch with its work
        # is a better outcome than a tidy branch and no work.
        with self.env:
            provisioned, clone = self._worked_on_task()
            with mock.patch.object(
                _Service, '_stash_before_forced_restore',
                side_effect=RuntimeError('stash failed'),
            ):
                self.service.restore_task_repositories(provisioned, force=True)
        self.assertEqual(
            (clone / 'form_service.py').read_text(encoding='utf-8'), 'PAGES = 99\n',
            'work was destroyed even though it could not be stashed',
        )

    def test_a_stale_index_lock_does_not_abort_the_restore(self) -> None:
        # Fail-closed must not become fail-often. `git stash` under a stale
        # lock reports only "could not write index", which the shared
        # stale-lock recovery does not recognise, so the first version of
        # this stash turned a recoverable restore into a hard abort.
        with self.env:
            provisioned, clone = self._worked_on_task()
            (clone / '.git' / 'index.lock').write_text('stale\n', encoding='utf-8')
            self.service.restore_task_repositories(provisioned, force=True)
        self.assertEqual(_current_branch(clone), 'master')
        self.assertFalse((clone / '.git' / 'index.lock').exists())
        stashes = subprocess.run(
            ['git', 'stash', 'list'], cwd=str(clone),
            capture_output=True, text=True,
        ).stdout
        self.assertIn(
            'form-core-lib', stashes,
            'the work was discarded rather than parked on the retry path',
        )

    def test_a_CLEAN_repo_is_still_restored_normally(self) -> None:
        # The feature still works: nothing to preserve, so the branch is
        # simply put back.
        with self.env:
            provisioned = self._pick_up_task()
            self.service.restore_task_repositories(provisioned, force=True)
        self.assertEqual(_current_branch(Path(provisioned[0].local_path)), 'master')


class StrandedCloneIsRecoveredBySyncTests(TaskPickupEndToEndTests):
    """A repo already in the workspace but sitting on the default branch.

    Sync only branch-prepped the repos it had just added, and returned early
    when nothing was missing. So a repo that got registered but never made
    it onto the task branch — its prep failed once, or a run died — stayed
    on master permanently: sync reported "already present", push and PR kept
    skipping it because the task branch did not exist, and clicking Sync
    again changed nothing. Reported as "it adds the repos and does the same
    thing, keeps it on master".

    Real git, real RepositoryService, real TaskRepositoryService.
    """

    def _task_service(self):
        """A real object, not a double: the task as the tracker returns it."""
        task = SimpleNamespace(id=TASK_BRANCH, tags=[], description='', summary='s')
        return SimpleNamespace(
            list_all_assigned_tasks=lambda: [task],
            get_assigned_tasks=lambda: [],
            get_review_tasks=lambda: [],
        )

    def _sync_service(self):
        from kato_core_lib.data_layers.service.task_repository_service import (
            TaskRepositoryService,
        )
        self.service._repositories = [self.repository]
        return TaskRepositoryService(
            repository_service=self.service,
            task_service=self._task_service(),
            workspace_manager=self.workspace,
            logger=self.service.logger,
        )

    def _strand_the_clone(self):
        """Clone into the workspace but leave it on master, as a failed
        prep would."""
        clone = self.workspaces / TASK_BRANCH / 'form-core-lib'
        clone.parent.mkdir(parents=True, exist_ok=True)
        _git(clone.parent, 'clone', '-q', str(self.remote), 'form-core-lib')
        self.assertEqual(_current_branch(clone), 'master')
        return clone

    def test_sync_moves_a_stranded_clone_ONTO_the_task_branch(self) -> None:
        clone = self._strand_the_clone()
        with self.env:
            self._sync_service().sync_task_repositories(TASK_BRANCH)
        self.assertEqual(
            _current_branch(clone), TASK_BRANCH,
            'a repo already in the workspace was left on master',
        )

    def test_the_recovery_keeps_the_code_and_any_work_in_progress(self) -> None:
        # The stranded clone may hold the agent's uncommitted work, so the
        # move must be a plain checkout, never a reset to the destination.
        clone = self._strand_the_clone()
        (clone / 'form_service.py').write_text('AGENT WORK\n', encoding='utf-8')
        with self.env:
            self._sync_service().sync_task_repositories(TASK_BRANCH)
        self.assertEqual(_current_branch(clone), TASK_BRANCH)
        self.assertEqual(
            (clone / 'form_service.py').read_text(encoding='utf-8'), 'AGENT WORK\n',
        )

    def test_the_operator_source_checkout_is_still_untouched(self) -> None:
        self._strand_the_clone()
        with self.env:
            self._sync_service().sync_task_repositories(TASK_BRANCH)
        self.assertNotIn(TASK_BRANCH, _branches(self.source))
        self.assertEqual(_current_branch(self.source), 'master')

    def test_the_recovery_itself_REFUSES_a_source_tree_path(self) -> None:
        # Defence in depth, tested directly because nothing reaches it once
        # the caller resolves workspace paths correctly. It matters anyway:
        # this method creates a branch WITHOUT going through
        # prepare_task_branches, so it does not inherit that guard — and a
        # caller holding an inventory object is exactly how the operator's
        # own checkout got branched the first time this was wired up.
        before = _branches(self.source)
        with self.env:
            reason = self.service.recover_clone_onto_task_branch(
                self.repository, TASK_BRANCH,   # local_path = the SOURCE tree
            )
        self.assertTrue(reason, 'the recovery accepted a source-tree path')
        self.assertIn('source tree', reason)
        self.assertEqual(
            _branches(self.source), before,
            'the recovery branched the operator source checkout',
        )
        self.assertEqual(_current_branch(self.source), 'master')

    def test_a_clone_ALREADY_on_the_task_branch_is_reported_clean(self) -> None:
        # The normal case must stay quiet — no failures, no churn.
        with self.env:
            self._pick_up_task()
            result = self._sync_service().sync_task_repositories(TASK_BRANCH)
        self.assertEqual(result['failed_repositories'], [])

    def test_a_clone_with_its_OWN_commits_is_refused_and_reported(self) -> None:
        # Moving those commits is a rebase or cherry-pick; kato refuses and
        # says so rather than doing it silently behind a Sync button.
        clone = self._strand_the_clone()
        (clone / 'form_service.py').write_text('LOCAL COMMIT\n', encoding='utf-8')
        _git(clone, 'config', 'user.email', 't@example.com')
        _git(clone, 'config', 'user.name', 'test')
        _git(clone, 'commit', '-aqm', 'own work on master')
        with self.env:
            result = self._sync_service().sync_task_repositories(TASK_BRANCH)
        self.assertFalse(result['synced'])
        self.assertEqual(len(result['failed_repositories']), 1)
        self.assertIn(
            'not on the task branch', result['failed_repositories'][0]['error'],
        )
        self.assertEqual(_current_branch(clone), 'master')


if __name__ == '__main__':
    unittest.main()
