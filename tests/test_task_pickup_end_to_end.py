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
        # Resolving pull-request API credentials needs a live provider and
        # is unrelated to every git behaviour these tests cover.
        self._prepare_repository_access = lambda repository: repository
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
        self.records: dict[str, list[str]] = {}

    def repository_path(self, task_id, repository_id):
        return self.root / str(task_id) / str(repository_id)

    def create(self, **kwargs):
        task_dir = self.root / str(kwargs.get('task_id'))
        task_dir.mkdir(parents=True, exist_ok=True)
        # Mirrors WorkspaceService.create: a non-empty list REPLACES the
        # stored ids. Reproduced faithfully so the union guard is actually
        # exercised rather than papered over by a forgiving double.
        ids = [str(r) for r in (kwargs.get('repository_ids') or []) if r]
        self.records[str(kwargs.get('task_id'))] = ids or self.records.get(
            str(kwargs.get('task_id')), [],
        )
        # The real WorkspaceService drops this sidecar beside the clones, and
        # ``_is_per_task_workspace_clone`` keys off it. Without it the harness
        # would look like a legacy shared checkout and the per-task guards
        # would silently never fire under test.
        (task_dir / '.kato-meta.json').write_text('{}', encoding='utf-8')

    def append_preflight_log(self, task_id, message):
        return None

    def update_status(self, task_id, status):
        self.status.append(status)

    # The sync path asks the workspace which repos it already holds.
    def get(self, task_id):
        task_dir = self.root / str(task_id)
        if not task_dir.is_dir():
            return None
        stored = self.records.get(str(task_id))
        if stored is None:
            stored = sorted(
                d.name for d in task_dir.iterdir() if (d / '.git').is_dir()
            )
        return SimpleNamespace(repository_ids=list(stored))


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

    def test_an_agent_deliverable_under_out_is_RECOVERABLE(self) -> None:
        # The artifact-discard path classified by bare top-level name
        # ({build, dist, out, coverage, target}) and then ran checkout -f +
        # clean -fd with NO stash. A repo whose deliverable genuinely lives
        # under one of those names — a static site in out/, a Maven module
        # in target/ — lost the agent's entire output with nothing to
        # recover from: no stash, no commit, no reflog entry.
        with self.env:
            provisioned = self._pick_up_task()
        clone = Path(provisioned[0].local_path)
        out = clone / 'out'
        out.mkdir()
        (out / 'index.html').write_text('<h1>the deliverable</h1>\n', encoding='utf-8')
        (out / 'notes.md').write_text('agent notes\n', encoding='utf-8')

        with self.env:
            self.service._discard_only_generated_artifacts(
                str(clone),
                self.service._working_tree_status(str(clone)),
                TASK_BRANCH,
            )

        # The tree is still cleaned — that behaviour is intentional.
        self.assertFalse(out.exists())
        # ...but it is no longer gone for good.
        stashes = subprocess.run(
            ['git', 'stash', 'list'], cwd=str(clone),
            capture_output=True, text=True,
        ).stdout
        self.assertTrue(
            stashes.strip(),
            'generated artifacts were deleted with no way back',
        )
        _git(clone, 'stash', 'apply', 'stash@{0}')
        self.assertEqual(
            (out / 'index.html').read_text(encoding='utf-8'),
            '<h1>the deliverable</h1>\n',
        )
        self.assertTrue((out / 'notes.md').is_file())

    def test_repo_prep_does_NOT_knock_a_workspace_clone_back_to_master(self) -> None:
        # Preflight ran _prepare_workspace_for_task at step 5, which puts a
        # clone back on the DESTINATION branch, while the task branch was
        # only created at step 8. Any early return in between — a task
        # description too thin to act on, one repo raising after earlier
        # ones were processed — left the whole workspace on master with no
        # task branch: "he will clone all the repos but will not create the
        # branch by the task name in them, all the repos will sit on
        # master."
        with self.env:
            provisioned = self._pick_up_task()
        clone = Path(provisioned[0].local_path)
        self.assertEqual(_current_branch(clone), TASK_BRANCH)

        # Step 5 in isolation, exactly as preflight calls it.
        with self.env:
            self.service._prepare_task_repository(provisioned[0])

        self.assertEqual(
            _current_branch(clone), TASK_BRANCH,
            'repository prep knocked the workspace clone back to master',
        )
        self.assertTrue((clone / 'form_service.py').is_file())

    def test_agent_work_survives_repo_prep_on_a_later_tick(self) -> None:
        # The same step, on a clone the agent has since worked in.
        with self.env:
            provisioned = self._pick_up_task()
            clone = Path(provisioned[0].local_path)
            (clone / 'form_service.py').write_text('AGENT WORK\n', encoding='utf-8')
            self.service._prepare_task_repository(provisioned[0])
        self.assertEqual(_current_branch(clone), TASK_BRANCH)
        self.assertEqual(
            (clone / 'form_service.py').read_text(encoding='utf-8'), 'AGENT WORK\n',
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


class ForcedRestoreParksWorkFirstTests(TaskPickupEndToEndTests):
    """A forced restore that DOES wipe must park the work first.

    Per-task workspace clones are now skipped entirely — see
    ``FailedTaskDoesNotTouchTheWorkspaceTests``. This class covers the case
    that still reaches the wipe: a LEGACY shared clone, the arrangement the
    restore was written for. There the reset is intended, and the stash is
    what keeps it from being destructive.

    The clone lives outside BOTH roots on purpose: inside the workspaces
    root it would be skipped as a per-task clone, and inside the source root
    the wipe guard would refuse it outright. Neither is the case under test.
    """

    def _worked_on_task(self):
        """A legacy clone with the agent's work in it."""
        legacy = self.root / 'legacy'
        legacy.mkdir(exist_ok=True)
        _git(legacy, 'clone', '-q', str(self.remote), 'form-core-lib')
        clone = legacy / 'form-core-lib'
        _git(clone, 'checkout', '-q', '-b', TASK_BRANCH)
        (clone / 'form_service.py').write_text('PAGES = 99\n', encoding='utf-8')
        (clone / 'brand_new.py').write_text('added by agent\n', encoding='utf-8')
        repository = SimpleNamespace(
            id='form-core-lib', local_path=str(clone),
            remote_url=str(self.remote), destination_branch='master',
        )
        return [repository], clone

    def test_a_CLEAN_legacy_clone_is_still_restored_normally(self) -> None:
        # The feature still works where it belongs: a shared checkout with
        # nothing to preserve is simply put back on the destination branch.
        # (Moved here from the workspace-clone tests, where asserting this
        # encoded the bug — an untouched per-task clone was being dragged to
        # master by a sibling repo's failure.)
        legacy = self.root / 'legacy'
        legacy.mkdir(exist_ok=True)
        _git(legacy, 'clone', '-q', str(self.remote), 'form-core-lib')
        clone = legacy / 'form-core-lib'
        _git(clone, 'checkout', '-q', '-b', TASK_BRANCH)
        repository = SimpleNamespace(
            id='form-core-lib', local_path=str(clone),
            remote_url=str(self.remote), destination_branch='master',
        )
        with self.env:
            self.service.restore_task_repositories([repository], force=True)
        self.assertEqual(_current_branch(clone), 'master')

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


class FailedTaskDoesNotTouchTheWorkspaceTests(TaskPickupEndToEndTests):
    """A task failure must leave the per-task workspace clone alone."""

    def _worked_on_task(self):
        provisioned = self._pick_up_task()
        clone = Path(provisioned[0].local_path)
        (clone / 'form_service.py').write_text('PAGES = 99\n', encoding='utf-8')
        (clone / 'brand_new.py').write_text('added by agent\n', encoding='utf-8')
        return provisioned, clone

    def test_a_TASK_FAILURE_leaves_the_workspace_clone_alone(self) -> None:
        # THE REPORT, via the path the validation found. Every task failure
        # runs restore_task_repositories(force=True) over the prepared
        # repositories — which ARE the per-task workspace clones. Three of
        # the four restore sites already refuse to touch those; this was the
        # fourth, and the one every failure goes through. A task that failed
        # for any reason ended with its repos back on master and the ones
        # the agent had worked in emptied.
        with self.env:
            provisioned = self._worked_on_task()[0]
        clone = Path(provisioned[0].local_path)
        with self.env:
            self.service.restore_task_repositories(provisioned, force=True)
        self.assertEqual(
            _current_branch(clone), TASK_BRANCH,
            'a task failure knocked the workspace clone back to master',
        )
        self.assertEqual(
            (clone / 'form_service.py').read_text(encoding='utf-8'), 'PAGES = 99\n',
        )
        self.assertTrue((clone / 'brand_new.py').is_file())

    def test_a_CLEAN_workspace_clone_is_not_dragged_to_master_either(self) -> None:
        # The early return needs current_branch == destination_branch, which
        # a clone on its task branch never satisfies — so an untouched repo
        # was moved to master too, dragged there by a sibling's failure.
        with self.env:
            provisioned = self._pick_up_task()
            clone = Path(provisioned[0].local_path)
            self.assertEqual(_current_branch(clone), TASK_BRANCH)
            self.service.restore_task_repositories(provisioned, force=True)
        self.assertEqual(_current_branch(clone), TASK_BRANCH)

    def test_repeated_failures_do_not_pile_up_stashes(self) -> None:
        # No self-healing before: every 180s tick re-wiped the tree into
        # another stash and left the clone on master forever.
        with self.env:
            provisioned = self._worked_on_task()[0]
            for _ in range(3):
                self.service.restore_task_repositories(provisioned, force=True)
        clone = Path(provisioned[0].local_path)
        stashes = subprocess.run(
            ['git', 'stash', 'list'], cwd=str(clone),
            capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(stashes, '', f'restores piled up stashes: {stashes}')
        self.assertEqual(_current_branch(clone), TASK_BRANCH)



class WorkspaceMetadataNeverShrinksTests(TaskPickupEndToEndTests):
    """Provisioning must never make a workspace forget a repo.

    ``WorkspaceService.create`` overwrites ``repository_ids`` whenever the
    list it is handed is non-empty. Any provisioning whose resolution returns
    a SUBSET of what is already on disk therefore erased the rest: adding one
    repo through the Files tab took a workspace from ``['alpha','beta']`` to
    ``['gamma']``. The clones stayed on disk, on their task branch, holding
    the agent's work — but kato had forgotten them, so push and pull-request
    silently skipped them, and re-syncing never repaired it.
    """

    def _second_repo(self):
        remote = self.root / 'remote' / 'event-core-lib'
        remote.mkdir(parents=True)
        _git(remote, 'init', '-q', '-b', 'master')
        _git(remote, 'config', 'user.email', 't@example.com')
        _git(remote, 'config', 'user.name', 'test')
        (remote / 'events.py').write_text('E = 2\n', encoding='utf-8')
        _git(remote, 'add', '-A')
        _git(remote, 'commit', '-qm', 'initial')
        return SimpleNamespace(
            id='event-core-lib', local_path=str(self.source_root / 'event-core-lib'),
            remote_url=str(remote), destination_branch='master',
        )

    def test_provisioning_a_SUBSET_keeps_the_repos_already_there(self) -> None:
        # THE REGRESSION, in its simplest form.
        with self.env:
            provision_task_workspace_clones(
                self.workspace, self.service,
                SimpleNamespace(id=TASK_BRANCH, summary='s', description='d'),
                [self.repository, self._second_repo()],
            )
            before = set(self.workspace.get(TASK_BRANCH).repository_ids)
            self.assertEqual(before, {'form-core-lib', 'event-core-lib'})

            # A later provisioning that resolves to only ONE of them.
            provision_task_workspace_clones(
                self.workspace, self.service,
                SimpleNamespace(id=TASK_BRANCH, summary='s', description='d'),
                [self.repository],
            )
            after = set(self.workspace.get(TASK_BRANCH).repository_ids)

        self.assertEqual(
            after, before,
            f'the workspace forgot {before - after} — push and PR will skip them',
        )

    def test_a_newly_added_repo_is_still_recorded(self) -> None:
        # The union must ADD, not just refuse to remove.
        with self.env:
            provision_task_workspace_clones(
                self.workspace, self.service,
                SimpleNamespace(id=TASK_BRANCH, summary='s', description='d'),
                [self.repository],
            )
            provision_task_workspace_clones(
                self.workspace, self.service,
                SimpleNamespace(id=TASK_BRANCH, summary='s', description='d'),
                [self.repository, self._second_repo()],
            )
            ids = set(self.workspace.get(TASK_BRANCH).repository_ids)
        self.assertEqual(ids, {'form-core-lib', 'event-core-lib'})

    def test_no_duplicates_accumulate_across_ticks(self) -> None:
        # The scan loop re-provisions every tick; the union must be stable.
        with self.env:
            for _ in range(3):
                provision_task_workspace_clones(
                    self.workspace, self.service,
                    SimpleNamespace(id=TASK_BRANCH, summary='s', description='d'),
                    [self.repository],
                )
            ids = self.workspace.get(TASK_BRANCH).repository_ids
        self.assertEqual(ids, ['form-core-lib'])


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

    def test_the_WIPE_PRIMITIVE_refuses_the_source_tree(self) -> None:
        # The guard was on the wrong function. `_refuse_branching_the_source_tree`
        # was wired into the two paths that merely CREATE A BRANCH, while
        # `_make_git_ready_for_work` — checkout -f, reset --hard, clean -fd —
        # had no source-tree gate at all. So kato refused to create a branch
        # in the operator's checkout and then wiped that same folder.
        (self.source / 'form_service.py').write_text(
            'OPERATOR EDIT\n', encoding='utf-8',
        )
        (self.source / 'scratch.txt').write_text('notes\n', encoding='utf-8')
        with self.env, self.assertRaises(RuntimeError) as caught:
            self.service._make_git_ready_for_work(
                str(self.source), 'master', self.repository,
            )
        self.assertIn('source tree', str(caught.exception))
        self.assertEqual(
            (self.source / 'form_service.py').read_text(encoding='utf-8'),
            'OPERATOR EDIT\n',
            'the operator edit was destroyed',
        )
        self.assertTrue(
            (self.source / 'scratch.txt').is_file(),
            'an untracked operator file was deleted',
        )

    def test_local_COMMITS_are_checked_before_the_reset_not_after(self) -> None:
        # The "you have N local commits, refusing to start a new task" guard
        # ran AFTER reset --hard, so on a dirty tree it could never fire —
        # the commits were already gone. And the stash parks the working
        # tree only: a commit is not stashed, so that loss was unrecoverable
        # outside the reflog.
        clone = self.workspaces / TASK_BRANCH / 'form-core-lib'
        clone.parent.mkdir(parents=True, exist_ok=True)
        _git(clone.parent, 'clone', '-q', str(self.remote), 'form-core-lib')
        _git(clone, 'config', 'user.email', 't@example.com')
        _git(clone, 'config', 'user.name', 'test')
        (clone / 'form_service.py').write_text('COMMITTED WORK\n', encoding='utf-8')
        _git(clone, 'commit', '-aqm', 'local commit not on origin')
        head = subprocess.run(
            ['git', 'rev-parse', 'HEAD'], cwd=str(clone),
            capture_output=True, text=True,
        ).stdout.strip()
        # ...and a dirty tree on top, which is what used to mask the guard.
        (clone / 'form_service.py').write_text('AND UNCOMMITTED\n', encoding='utf-8')

        with self.env, self.assertRaises(RuntimeError) as caught:
            self.service._make_git_ready_for_work(str(clone), 'master', None)
        self.assertIn('local commit', str(caught.exception))
        self.assertEqual(
            subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=str(clone),
                           capture_output=True, text=True).stdout.strip(),
            head,
            'the local commit was discarded before the guard could refuse',
        )

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


class OneBadRepoMustNotStopTheRestTests(TaskPickupEndToEndTests):
    """A per-repo fault must cost that repo only.

    ``prepare_task_branches`` was a bare loop: the first repo that raised
    ended the pass, and every repo AFTER it was never prepped — left on the
    remote's default branch, silently, with push and PR then skipping them
    because the task branch did not exist. One interrupted clone took out
    every repo behind it in the list.
    """

    def _clone_into_workspace(self, name, remote):
        target = self.workspaces / TASK_BRANCH / name
        target.parent.mkdir(parents=True, exist_ok=True)
        _git(target.parent, 'clone', '-q', str(remote), name)
        return SimpleNamespace(
            id=name, local_path=str(target),
            remote_url=str(remote), destination_branch='master',
        )

    def _broken_clone(self, name):
        """An interrupted clone: .git present, no objects, no refs."""
        target = self.workspaces / TASK_BRANCH / name
        (target / '.git' / 'objects' / 'pack').mkdir(parents=True)
        (target / '.git' / 'refs').mkdir(parents=True)
        return SimpleNamespace(
            id=name, local_path=str(target),
            remote_url=str(self.remote), destination_branch='master',
        )

    def test_a_broken_repo_does_not_strand_the_ones_after_it(self) -> None:
        # THE REGRESSION. 'good' comes AFTER the broken one in the list.
        broken = self._broken_clone('broken-core-lib')
        good = self._clone_into_workspace('form-core-lib', self.remote)
        with self.env, self.assertRaises(RuntimeError) as caught:
            self.service.prepare_task_branches(
                [broken, good],
                {'broken-core-lib': TASK_BRANCH, 'form-core-lib': TASK_BRANCH},
            )
        self.assertEqual(
            _current_branch(Path(good.local_path)), TASK_BRANCH,
            'a healthy repo was stranded on master by an unrelated failure',
        )
        # And it still fails loudly, naming what went wrong.
        self.assertIn('broken-core-lib', str(caught.exception))

    def test_the_error_names_EVERY_failure_not_just_the_first(self) -> None:
        first = self._broken_clone('broken-a')
        second = self._broken_clone('broken-b')
        with self.env, self.assertRaises(RuntimeError) as caught:
            self.service.prepare_task_branches(
                [first, second],
                {'broken-a': TASK_BRANCH, 'broken-b': TASK_BRANCH},
            )
        message = str(caught.exception)
        self.assertIn('broken-a', message)
        self.assertIn('broken-b', message)

    def test_all_healthy_repos_still_succeed_silently(self) -> None:
        a = self._clone_into_workspace('form-core-lib', self.remote)
        with self.env:
            self.service.prepare_task_branches(
                [a], {'form-core-lib': TASK_BRANCH},
            )
        self.assertEqual(_current_branch(Path(a.local_path)), TASK_BRANCH)


class InterruptedCloneIsRecloneableTests(TaskPickupEndToEndTests):
    """A clone with no objects must not be trusted forever."""

    def _interrupted(self):
        target = self.workspaces / TASK_BRANCH / 'form-core-lib'
        (target / '.git' / 'objects' / 'pack').mkdir(parents=True)
        (target / '.git' / 'refs').mkdir(parents=True)
        return target

    def test_an_interrupted_clone_is_removed_so_it_can_be_recloned(self) -> None:
        target = self._interrupted()
        repository = SimpleNamespace(
            id='form-core-lib', local_path=str(target),
            remote_url=str(self.remote), destination_branch='master',
        )
        with self.env:
            self.service.ensure_clone(repository, target)
        self.assertTrue(
            (target / 'form_service.py').is_file(),
            'the interrupted clone was reused instead of re-cloned',
        )

    def test_a_HEALTHY_clone_is_never_removed(self) -> None:
        target = self.workspaces / TASK_BRANCH / 'form-core-lib'
        target.parent.mkdir(parents=True, exist_ok=True)
        _git(target.parent, 'clone', '-q', str(self.remote), 'form-core-lib')
        (target / 'form_service.py').write_text('AGENT WORK\n', encoding='utf-8')
        repository = SimpleNamespace(
            id='form-core-lib', local_path=str(target),
            remote_url=str(self.remote), destination_branch='master',
        )
        with self.env:
            self.service.ensure_clone(repository, target)
        self.assertEqual(
            (target / 'form_service.py').read_text(encoding='utf-8'),
            'AGENT WORK\n',
            'a healthy clone was destroyed',
        )

    def test_a_repo_with_commits_but_no_checkout_is_restored_not_removed(self) -> None:
        # The other empty-tree case: objects ARE present, so restore the
        # working tree rather than throwing the clone away.
        target = self.workspaces / TASK_BRANCH / 'form-core-lib'
        target.parent.mkdir(parents=True, exist_ok=True)
        _git(target.parent, 'clone', '-q', str(self.remote), 'form-core-lib')
        for entry in target.iterdir():
            if entry.name != '.git':
                entry.unlink()
        repository = SimpleNamespace(
            id='form-core-lib', local_path=str(target),
            remote_url=str(self.remote), destination_branch='master',
        )
        with self.env:
            self.service.ensure_clone(repository, target)
        self.assertTrue((target / 'form_service.py').is_file())


class GuardsHoldOnADefaultInstallTests(TaskPickupEndToEndTests):
    """The source-tree guards must not depend on an optional env var.

    ``KATO_WORKSPACES_ROOT`` is documented as "Empty = ~/.kato/workspaces"
    and the default is applied internally, never written back to the
    environment. Reading the raw variable made BOTH guards return "not in
    the source tree" for every path on a normal install — the protection was
    believed to be on and was not. The tests missed it by setting both vars.
    """

    def test_the_guard_fires_with_only_the_source_root_configured(self) -> None:
        with mock.patch.dict(os.environ, {
            'REPOSITORY_ROOT_PATH': str(self.source_root),
            'KATO_WORKSPACES_ROOT': '',
        }):
            self.assertIsNotNone(
                self.service._source_tree_containing(str(self.source)),
                'the source tree was unguarded on a default install',
            )

    def test_branch_prep_still_REFUSES_the_source_tree_by_default(self) -> None:
        before = _branches(self.source)
        with mock.patch.dict(os.environ, {
            'REPOSITORY_ROOT_PATH': str(self.source_root),
            'KATO_WORKSPACES_ROOT': '',
        }), self.assertRaises(RuntimeError):
            self.service.prepare_task_branches(
                [self.repository], {'form-core-lib': TASK_BRANCH},
            )
        self.assertEqual(_branches(self.source), before)

    def test_the_default_workspaces_root_is_not_treated_as_source(self) -> None:
        # A clone under the DEFAULT workspaces root must stay allowed.
        default_clone = Path.home() / '.kato' / 'workspaces' / 'T1' / 'repo'
        with mock.patch.dict(os.environ, {
            'REPOSITORY_ROOT_PATH': str(Path.home()),
            'KATO_WORKSPACES_ROOT': '',
        }):
            self.assertIsNone(
                self.service._source_tree_containing(str(default_clone)),
            )


if __name__ == '__main__':
    unittest.main()
