"""Two ways a workspace clone silently stops being a workspace clone.

Both were reported together, both are silent, and both end with the agent
working somewhere it should not be:

1. **Half-finished clone reused forever.** ``git clone`` creates ``.git``
   first and checks the working tree out afterwards. Interrupt it between
   the two — killed process, dropped network, full disk — and the folder
   holds nothing but ``.git``. ``ensure_clone`` asked only "does .git
   exist", so every later run agreed the repo was "already on disk,
   reusing" and it stayed empty permanently: "event-core-lib cloned but is
   empty — only a .git directory, no checked-out files".

2. **Falling back to the operator's own checkouts.** When cloning raised,
   wait-planning caught it and carried on with the INVENTORY repository
   objects, whose ``local_path`` is the operator's source tree. The task
   branch was then created in the folders the operator works in every day,
   and the agent's cwd pointed at them: "this kato release creates branches
   inside the dev repo... and keeps the task repos on master that claude
   works on".

``task_preflight_service`` had already been hardened against (2) — its
comment says "hard-fail is the only safe default for a workspace-mode
install". Wait-planning shares the same helper and was left with the old
fallback, so the autonomous flow was safe and the chat flow was not. That
asymmetry is what these tests exist to prevent.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from kato_core_lib.data_layers.service.repository_service import RepositoryService
from kato_core_lib.data_layers.service.wait_planning_service import WaitPlanningService


def _git(cwd, *args):
    subprocess.run(
        ['git', *args], cwd=str(cwd), check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


class _Service(RepositoryService):
    """Real git, no network — only the seams ensure_clone touches."""

    def __init__(self):
        self.logger = SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None,
            exception=lambda *a, **k: None, error=lambda *a, **k: None,
        )
        self.cloned: list[str] = []

    def _clone_speedup_args(self, repository, target):  # noqa: ARG002
        return []


class HalfFinishedCloneTests(unittest.TestCase):
    """A clone with .git but no files must be repaired, not trusted."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.service = _Service()

        # A real repository with one real committed file.
        self.origin = self.root / 'origin'
        self.origin.mkdir()
        _git(self.origin, 'init', '-q')
        _git(self.origin, 'config', 'user.email', 't@example.com')
        _git(self.origin, 'config', 'user.name', 'test')
        (self.origin / 'service.py').write_text('x = 1\n', encoding='utf-8')
        _git(self.origin, 'add', '-A')
        _git(self.origin, 'commit', '-qm', 'first')

        self.clone = self.root / 'event-core-lib'
        self.repository = SimpleNamespace(
            id='event-core-lib', local_path=str(self.clone),
            remote_url=str(self.origin),
        )

    def _clone_then_empty_the_worktree(self):
        """Reproduce an interrupted clone: .git present, no checked-out files."""
        _git(self.root, 'clone', '-q', str(self.origin), 'event-core-lib')
        for entry in self.clone.iterdir():
            if entry.name != '.git':
                entry.unlink()
        self.assertEqual(
            [e.name for e in self.clone.iterdir()], ['.git'],
            'setup failed to reproduce the empty-clone state',
        )

    def test_an_empty_clone_is_RESTORED_not_reused(self) -> None:
        # THE REPORT. Reusing it leaves the agent with no source files at all.
        self._clone_then_empty_the_worktree()
        self.service.ensure_clone(self.repository, self.clone)
        self.assertTrue(
            (self.clone / 'service.py').is_file(),
            'the working tree was not restored — the clone is still empty',
        )
        self.assertEqual(
            (self.clone / 'service.py').read_text(encoding='utf-8'), 'x = 1\n',
        )

    def test_the_repair_is_a_checkout_not_a_re_clone(self) -> None:
        # The objects are already on disk. Re-cloning would throw away
        # whatever the interrupted attempt did manage to fetch, over the
        # network, on a path that runs for every repo of every task.
        self._clone_then_empty_the_worktree()
        head_before = subprocess.run(
            ['git', 'rev-parse', 'HEAD'], cwd=str(self.clone),
            capture_output=True, text=True,
        ).stdout.strip()
        self.service.ensure_clone(self.repository, self.clone)
        head_after = subprocess.run(
            ['git', 'rev-parse', 'HEAD'], cwd=str(self.clone),
            capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(head_before, head_after)

    def test_a_HEALTHY_clone_is_left_completely_alone(self) -> None:
        # The repair must never touch a clone that has files — that is where
        # the agent's uncommitted work lives, and the repair uses -f.
        _git(self.root, 'clone', '-q', str(self.origin), 'event-core-lib')
        (self.clone / 'service.py').write_text('EDITED BY AGENT\n', encoding='utf-8')
        (self.clone / 'brand-new.py').write_text('new\n', encoding='utf-8')
        self.service.ensure_clone(self.repository, self.clone)
        self.assertEqual(
            (self.clone / 'service.py').read_text(encoding='utf-8'),
            'EDITED BY AGENT\n',
            'the repair discarded uncommitted agent work',
        )
        self.assertTrue((self.clone / 'brand-new.py').is_file())

    def test_a_repository_with_no_commits_is_not_an_error(self) -> None:
        # An unborn HEAD looks identical on disk (only .git). It is a
        # legitimate state and must not fail the task.
        self.clone.mkdir()
        _git(self.clone, 'init', '-q')
        self.service.ensure_clone(self.repository, self.clone)
        self.assertTrue((self.clone / '.git').is_dir())

    def test_a_missing_clone_still_clones(self) -> None:
        # The method's actual job, unaffected by the repair branch.
        self.service.ensure_clone(self.repository, self.clone)
        self.assertTrue((self.clone / 'service.py').is_file())


class ALiveCloneIsNeverDeletedTests(unittest.TestCase):
    """Two provisioning runs for one task must not destroy each other's clone.

    Reported on UNA-2417, from the Files-tab Sync button::

        error: unable to write file …/.git/objects/pack/pack-<sha>.pack:
               No such file or directory
        fatal: unable to rename temporary '*.pack' file to …

    The directory vanished mid-clone, and kato deleted it. A clone that is
    still downloading is indistinguishable on disk from an abandoned one —
    empty working tree, no refs, nothing under ``objects/pack`` but the
    ``tmp_pack_*`` stream — so the interrupted-clone repair treated a LIVE
    clone as garbage. The Sync button and the scan tick's reconcile share no
    lock, so both were cloning the same path at once.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.service = _Service()
        self.origin = self.root / 'origin'
        self.origin.mkdir()
        _git(self.origin, 'init', '-q')
        _git(self.origin, 'config', 'user.email', 't@example.com')
        _git(self.origin, 'config', 'user.name', 'test')
        (self.origin / 'service.py').write_text('x = 1\n', encoding='utf-8')
        _git(self.origin, 'add', '-A')
        _git(self.origin, 'commit', '-qm', 'first')
        self.clone = self.root / 'objective_love_core_lib'
        self.repository = SimpleNamespace(
            id='objective_love_core_lib', local_path='',
            remote_url=str(self.origin),
        )

    def _downloading_clone(self, *, age_seconds: float = 0.0) -> Path:
        """A directory in the exact state git leaves while fetching a pack."""
        pack = self.clone / '.git' / 'objects' / 'pack'
        pack.mkdir(parents=True)
        (self.clone / '.git' / 'objects' / 'info').mkdir()
        stream = pack / 'tmp_pack_9f3c1a'
        stream.write_bytes(b'PACK partial')
        if age_seconds:
            old = time.time() - age_seconds
            os.utime(stream, (old, old))
        return stream

    def test_a_clone_that_is_still_downloading_is_never_deleted(self) -> None:
        # THE REPORT. The other thread's git dies the moment this directory
        # goes away, with the pack-file error above.
        stream = self._downloading_clone()
        self.service.ensure_clone(self.repository, self.clone)
        self.assertTrue(
            self.clone.is_dir(), 'the live clone directory was deleted',
        )
        self.assertTrue(stream.is_file(), 'the in-flight pack was deleted')

    def test_an_empty_scratch_file_is_a_dead_clone_not_a_live_one(self) -> None:
        # The documented shape of a clone killed mid-fetch: a 0-byte
        # tmp_pack, written moments ago. It must still be repaired — this is
        # what the merge-repair path depends on.
        pack = self.clone / '.git' / 'objects' / 'pack'
        pack.mkdir(parents=True)
        (self.clone / '.git' / 'objects' / 'info').mkdir()
        (pack / 'tmp_pack_HJydSJ').write_bytes(b'')
        self.service.ensure_clone(self.repository, self.clone)
        self.assertTrue(
            (self.clone / 'service.py').is_file(),
            'the killed clone was not replaced with a real one',
        )

    def test_an_abandoned_download_is_still_removed_and_re_cloned(self) -> None:
        # The repair this guard narrows must keep working: a clone whose temp
        # pack has not been touched for minutes is genuinely dead.
        self._downloading_clone(age_seconds=600)
        self.service.ensure_clone(self.repository, self.clone)
        self.assertTrue(
            (self.clone / 'service.py').is_file(),
            'the abandoned clone was not replaced with a real one',
        )

    def test_two_clones_of_the_same_path_are_serialised(self) -> None:
        """The lock: the reuse check and the clone are ONE decision.

        Without it the second caller inspects the first one's half-written
        directory — which is how the repair got the chance to delete it.
        """
        started = threading.Event()
        order: list[str] = []
        real_run_git = self.service._run_git

        def slow_clone(cwd, args, message, repository=None, **kwargs):
            if args and args[0] == 'clone':
                order.append('clone-start')
                # The state the second caller used to walk in on.
                self._downloading_clone()
                started.set()
                time.sleep(0.4)
                shutil.rmtree(self.clone, ignore_errors=True)
                result = real_run_git(cwd, args, message, repository, **kwargs)
                order.append('clone-end')
                return result
            return real_run_git(cwd, args, message, repository, **kwargs)

        self.service._run_git = slow_clone
        failures: list[Exception] = []

        def second_caller():
            started.wait(timeout=5)
            try:
                self.service.ensure_clone(self.repository, self.clone)
                order.append('second-done')
            except Exception as exc:  # noqa: BLE001 - reported below
                failures.append(exc)

        other = threading.Thread(target=second_caller)
        other.start()
        self.service.ensure_clone(self.repository, self.clone)
        other.join(timeout=10)

        self.assertEqual(failures, [], f'the second clone failed: {failures}')
        self.assertEqual(
            order, ['clone-start', 'clone-end', 'second-done'],
            'the second caller ran while the first was still cloning',
        )
        self.assertTrue((self.clone / 'service.py').is_file())


class FreshCloneMustHaveAWorkingTreeTests(unittest.TestCase):
    """A clone exiting 0 is not proof of a usable checkout.

    ``--reference-if-able ... --dissociate`` does real work after the objects
    arrive; interrupted there, a FRESH clone lands in the same ``.git``-only
    state the reuse path had to learn about. The agent then opens a repo with
    no source in it: "he will just delete the entire code from some repos".
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.origin = self.root / 'origin'
        self.origin.mkdir()
        _git(self.origin, 'init', '-q')
        _git(self.origin, 'config', 'user.email', 't@example.com')
        _git(self.origin, 'config', 'user.name', 'test')
        (self.origin / 'service.py').write_text('x = 1\n', encoding='utf-8')
        _git(self.origin, 'add', '-A')
        _git(self.origin, 'commit', '-qm', 'first')

    def test_a_fresh_clone_is_verified_to_have_files(self) -> None:
        clone = self.root / 'event-core-lib'
        service = _Service()
        repository = SimpleNamespace(
            id='event-core-lib', local_path=str(clone), remote_url=str(self.origin),
        )
        # Emulate a clone that produced .git but no checkout, then let
        # ensure_clone's post-clone verification run.
        real_run_git = service._run_git

        def clone_then_strip(local_path, args, message, repo=None):
            result = real_run_git(local_path, args, message, repo)
            if args and args[0] == 'clone' and clone.is_dir():
                for entry in clone.iterdir():
                    if entry.name != '.git':
                        entry.unlink()
            return result

        service._run_git = clone_then_strip
        service.ensure_clone(repository, clone)
        self.assertTrue(
            (clone / 'service.py').is_file(),
            'a fresh clone was left without a working tree',
        )


class CloneFailureReachesTheUiTests(unittest.TestCase):
    """A clone failure has to reach the operator, not just the log file.

    ``append_preflight_log`` is replayed into ONE task's session stream, so
    it is only ever seen by someone already looking at that chat — and a
    clone failure is exactly the case where that chat has no files to show.
    The mission-log line is the one the status feed carries and
    ``classifyStatusEntry`` turns into a notification.

    Deliberately reuses the existing pipeline: no new endpoint, no new
    transport, just the mission-log shape the UI already reads.
    """

    def _workspace(self):
        calls = {'preflight': [], 'status': []}
        return SimpleNamespace(
            create=lambda **kw: None,
            repository_path=lambda task_id, repo_id: Path('/ws') / task_id / repo_id,
            append_preflight_log=lambda t, m: calls['preflight'].append(m),
            update_status=lambda t, s: calls['status'].append(s),
            calls=calls,
        )

    def _provision_failing(self, workspace):
        from kato_core_lib.data_layers.service import (
            workspace_provisioning_service as module,
        )
        repository_service = SimpleNamespace(
            ensure_clone=lambda repo, path: (_ for _ in ()).throw(
                RuntimeError('fatal: could not read from remote repository'),
            ),
        )
        with self.assertRaises(RuntimeError):
            module.provision_task_workspace_clones(
                workspace,
                repository_service,
                SimpleNamespace(id='UNA-3025', summary='s', description='d'),
                [SimpleNamespace(id='event-core-lib', local_path='/src/event-core-lib')],
            )

    def test_the_failure_is_logged_in_the_shape_the_ui_classifies(self) -> None:
        workspace = self._workspace()
        with self.assertLogs(
            'kato_core_lib.data_layers.service.workspace_provisioning_service',
            level='INFO',
        ) as logs:
            self._provision_failing(workspace)
        self.assertTrue(
            any('Mission UNA-3025: repository clone failed:' in line
                for line in logs.output),
            f'no UI-classifiable clone-failure line was emitted: {logs.output}',
        )

    def test_the_error_says_WHICH_repository_failed(self) -> None:
        """UNA-2417: one repo's git error was reported under another's name.

        Provisioning handed back a single joined message, so every caller that
        reports per repository had to blame all of them — the Files-tab sync
        listed ``ob-love-ui`` as having failed with
        ``objective_love_core_lib``'s pack error. The failures are carried per
        repository, and only the repo that failed is in them.
        """
        from kato_core_lib.data_layers.service import (
            workspace_provisioning_service as module,
        )
        workspace = self._workspace()

        def ensure_clone(repository, path):  # noqa: ARG001 - fake
            if repository.id == 'objective_love_core_lib':
                raise RuntimeError('unable to write file …pack-7c97.pack')

        with self.assertRaises(module.WorkspaceCloneError) as caught:
            module.provision_task_workspace_clones(
                workspace,
                SimpleNamespace(ensure_clone=ensure_clone),
                SimpleNamespace(id='UNA-2417', summary='s', description='d'),
                [
                    SimpleNamespace(id='ob-love-ui', local_path='/src/ob-love-ui'),
                    SimpleNamespace(
                        id='objective_love_core_lib',
                        local_path='/src/objective_love_core_lib',
                    ),
                ],
            )
        self.assertEqual(
            caught.exception.failures,
            {'objective_love_core_lib': 'unable to write file …pack-7c97.pack'},
        )
        # The joined message stays — it is what the log and the chat show.
        self.assertIn('failed to clone 1 of 2', str(caught.exception))

    def test_the_workspace_is_still_marked_errored(self) -> None:
        # The notification is ADDITIONAL to the existing signals, not a
        # replacement for them.
        workspace = self._workspace()
        self._provision_failing(workspace)
        self.assertIn('errored', workspace.calls['status'])
        self.assertTrue(
            any('clone failed' in m for m in workspace.calls['preflight']),
        )


class WaitPlanningNeverUsesSourceCheckoutsTests(unittest.TestCase):
    """A clone failure must not hand back the operator's own repositories."""

    def _service(self, *, workspace_manager):
        service = WaitPlanningService.__new__(WaitPlanningService)
        service._workspace_manager = workspace_manager
        service._repository_service = SimpleNamespace()
        service.logger = SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None,
            exception=lambda *a, **k: None, error=lambda *a, **k: None,
        )
        return service

    @staticmethod
    def _source_repos():
        # What the INVENTORY holds: the operator's live checkouts.
        return [SimpleNamespace(id='form-core-lib', local_path='/Codes/dev-una/form-core-lib')]

    def _provision_with_failure(self, service, repositories):
        import kato_core_lib.data_layers.service.wait_planning_service as module
        original = module.provision_task_workspace_clones

        def boom(*args, **kwargs):  # noqa: ARG001
            raise RuntimeError('clone failed: connection reset')

        module.provision_task_workspace_clones = boom
        try:
            return service._provision_workspace(
                SimpleNamespace(id='UNA-3025'), repositories,
            )
        finally:
            module.provision_task_workspace_clones = original

    def test_a_clone_failure_yields_NO_repos_not_the_source_ones(self) -> None:
        # THE REGRESSION. Returning the inventory repos put the task branch
        # and the agent's cwd inside the operator's source tree.
        service = self._service(workspace_manager=SimpleNamespace())
        result = self._provision_with_failure(service, self._source_repos())
        self.assertEqual(result, [])

    def test_the_source_path_never_survives_the_failure(self) -> None:
        # Stated as a path assertion too: the failure mode was not "a wrong
        # count", it was "these specific directories got branched".
        service = self._service(workspace_manager=SimpleNamespace())
        result = self._provision_with_failure(service, self._source_repos())
        for repo in result:
            self.assertNotIn('dev-una', str(getattr(repo, 'local_path', '')))

    def test_a_legacy_single_clone_install_still_gets_its_repos(self) -> None:
        # With no workspace manager the helper is a documented no-op and the
        # inventory clone IS the only checkout — degrading to [] there would
        # break every legacy install.
        service = self._service(workspace_manager=None)
        repositories = self._source_repos()
        result = self._provision_with_failure(service, repositories)
        self.assertEqual(result, repositories)


if __name__ == '__main__':
    unittest.main()
