from __future__ import annotations

import os
import threading
from types import SimpleNamespace
from pathlib import Path

from bitbucket_core_lib.bitbucket_core_lib.helpers.git_auth import git_http_auth_header
from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    PR_DESCRIPTION_FILENAME,
)
from git_core_lib.git_core_lib.client.git_client import GitClientMixin
from git_core_lib.git_core_lib.helpers.git_clean_utils import (
    generated_artifact_paths_from_status,
    git_ready_command_summary,
    status_contains_only_removable_artifacts,
    validation_report_paths_from_status,
)
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.data.fields import RepositoryFields
from utils_core_lib.utils_core_lib.text_utils import (
    normalized_text,
    text_from_attr,
)
from kato_core_lib.data_layers.service.repository_inventory_service import (
    RepositoryInventoryService,
)
from kato_core_lib.data_layers.service.repository_publication_service import (
    RepositoryPublicationService,
)
from kato_core_lib.data_layers.service.workspace_manager import (
    _KATO_METADATA_FILENAME,
)


class _NothingToCommit(Exception):
    """Every dirty path was a publication exclusion — nothing left to save.

    Distinct from a git failure: an empty index here is the correct outcome
    (the only dirty file was a validation report), not an error to report.
    """


def _is_per_task_workspace_clone(repository) -> bool:
    """True when ``repository.local_path`` is under a per-task kato workspace.

    Per-task clones live at ``<workspace_root>/<task_id>/<repo_id>/``
    next to a ``.kato-meta.json`` sidecar; legacy / shared clones live
    elsewhere on disk and don't carry the sidecar. We use this signal
    to keep per-task clones on the task branch across publish ops
    (the "restore to master after push" behavior is for shared clones).
    """
    local_path = str(getattr(repository, 'local_path', '') or '').strip()
    if not local_path:
        return False
    try:
        return (Path(local_path).parent / _KATO_METADATA_FILENAME).is_file()
    except OSError:
        return False


class RepositoryHasNoChangesError(RuntimeError):
    """Raised when a task branch has nothing to publish in a given repo.

    A typed exception (rather than a string-matched RuntimeError) lets
    the publisher tell ``"the work was a no-op for this repo"`` apart
    from genuine publish failures. The former is a normal outcome for
    multi-repo tasks where a repository is tagged for context but the
    agent didn't change any of its files; the latter blocks the task.
    """


class RepositoryService(GitClientMixin, RepositoryInventoryService):
    """Manage repository worktree preparation, branch publication, and cleanup."""

    def __init__(self, repositories_config, max_retries: int) -> None:
        super().__init__(repositories_config, max_retries)
        self._publication_service = RepositoryPublicationService(self, max_retries)
        # Serialises merge-finalisation so a burst of polled reads (Files
        # tab + Changes tab both refresh ~every 5s) can't race two commits
        # of the same pending merge. Finalisation is rare, so one lock for
        # the service is plenty.
        self._merge_finalize_lock = threading.Lock()

    def _build_git_http_auth_header(self, repository) -> str:
        return git_http_auth_header(
            repository,
            bitbucket_username_attr=RepositoryFields.BITBUCKET_USERNAME,
        )

    def prepare_task_repositories(self, repositories: list[object]) -> list[object]:
        self._validate_git_executable()
        return [
            self._prepare_task_repository(repository)
            for repository in repositories
        ]

    def ensure_clone(self, repository, target_path) -> None:
        """Clone the repo's remote into ``target_path`` if it isn't already.

        Idempotent: if ``target_path/.git`` exists the objects are already
        there, so the clone is skipped (the rest of the pipeline will fetch /
        reset / check out the task branch). Used by per-task workspace mode —
        each ticket gets its own clone-set so parallel tasks don't share
        branch state.

        ``.git`` existing is NOT proof the clone finished. ``git clone``
        creates the git directory first and checks the working tree out
        afterwards, so an interruption between the two — a killed process, a
        network drop, a full disk — leaves a folder holding nothing but
        ``.git``. This used to return on that state unconditionally, and
        because the check only ever asked "does .git exist", every later run
        agreed the repo was "already on disk, reusing". The clone stayed
        empty permanently and the agent reported it: "event-core-lib cloned
        but is empty — only a .git directory, no checked-out files".
        """
        self._validate_git_executable()
        target = Path(str(target_path))
        if (target / '.git').is_dir():
            self._restore_unchecked_out_clone(repository, target)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        remote_url = normalized_text(text_from_attr(repository, 'remote_url'))
        if not remote_url:
            raise ValueError(
                f'cannot clone repository {repository.id}: no remote_url configured'
            )
        # ``git -C <parent> clone <url> <name>`` keeps the call shape the
        # rest of this service uses. Auth is whatever the user has set
        # up on their host (ssh-agent, git credential helper, or token
        # baked into the URL); kato doesn't manage credentials at the
        # transport layer.
        self._run_git(
            str(target.parent),
            ['clone', *self._clone_speedup_args(repository, target), remote_url,
             target.name],
            f'failed to clone {repository.id} from {remote_url} into {target}',
            repository,
        )
        # A clone that exits 0 is still not proof of a usable checkout. The
        # reuse path below already had to learn this; a FRESH clone can land
        # the same way — ``--reference-if-able ... --dissociate`` does real
        # work after the objects arrive, and an interruption there leaves the
        # same ``.git``-only folder. Verified here too so the agent is never
        # handed an empty repository on the very first pickup: "he will just
        # delete the entire code from some repos".
        self._restore_unchecked_out_clone(repository, target)

    def _restore_unchecked_out_clone(self, repository, target: Path) -> None:
        """Check out a clone that has ``.git`` but no working files.

        The repair is a checkout, not a re-clone: the objects are already on
        disk, so materialising the tree costs nothing and keeps whatever the
        interrupted clone did manage to fetch.

        ``-f`` is safe here ONLY because of the emptiness test above it. The
        working tree is verified to contain nothing at all besides ``.git``
        before this runs, so there is provably no uncommitted work for the
        force to discard. Do not lift that guard: this same flag, reached
        without that proof, is what destroyed 41 files of an operator's work
        through ``_make_git_ready_for_work``.

        A repo that is legitimately empty (freshly created, no commits) hits
        this same branch and must NOT turn into a task failure, so an unborn
        HEAD returns quietly.

        A failed repair does raise. Past that point the clone is known to have
        commits and no files, which makes it unusable — handing that folder to
        the agent is what produced "cloned but is empty, only a .git
        directory". Raising routes it into
        ``provision_task_workspace_clones``'s existing handler, which marks
        the workspace errored and emits the mission-log line the UI turns into
        a notification. No new plumbing: the same path any clone failure takes.
        """
        try:
            if any(entry.name != '.git' for entry in target.iterdir()):
                return  # Files are present — the clone completed.
        except OSError:
            return
        try:
            head = self._git_stdout(
                str(target),
                ['rev-parse', '--verify', 'HEAD'],
                f'failed to read HEAD for {repository.id} at {target}',
                repository,
            )
        except Exception:
            # Unborn HEAD: either a genuinely empty repository, or a clone
            # that died before fetching anything. Neither is repairable by a
            # checkout, and re-cloning here would be guesswork.
            return
        if not normalized_text(head):
            return
        self.logger.warning(
            'workspace clone for %s at %s has no checked-out files; '
            'restoring the working tree from HEAD', repository.id, target,
        )
        self._run_git(
            str(target),
            ['checkout', '-f', 'HEAD'],
            f'failed to restore the working tree for {repository.id} at {target} '
            f'(the clone has commits but no checked-out files)',
            repository,
        )

    def _clone_speedup_args(self, repository, target: Path) -> list[str]:
        """Reuse the operator's existing checkout as a local object source.

        A per-task workspace clone re-downloaded the WHOLE history over the
        network every time, which is what made the Files-tab sync button feel
        broken on a large repo (~39s for a 144MB repo here, vs ~7s with this).
        The inventory already knows where that repo is checked out on disk —
        the objects are sitting right there, so the network only has to carry
        what the local copy is missing.

        ``--dissociate`` is NOT optional: without it the new clone keeps an
        ``objects/info/alternates`` pointer at the operator's working tree,
        and the workspace silently corrupts the day they delete that
        directory or it gets gc'd. Dissociating copies the borrowed objects
        in, so the result is byte-for-byte an ordinary independent clone —
        same history, same refs, no external dependency.

        ``--reference-if-able`` (not ``--reference``) so a stale or
        unreadable path degrades to a normal clone instead of failing it.
        """
        local_path = normalized_text(text_from_attr(repository, 'local_path'))
        if not local_path:
            return []
        reference = Path(local_path)
        try:
            # Must be a real repository, and never the clone we're creating.
            if not (reference / '.git').is_dir():
                return []
            if reference.resolve() == target.resolve():
                return []
        except OSError:
            return []
        return ['--reference-if-able', str(reference), '--dissociate']

    def restore_task_repositories(
        self,
        repositories: list[object],
        *,
        force: bool = False,
    ) -> list[object]:
        self._validate_git_executable()
        for repository in repositories:
            self._restore_task_repository(repository, force=force)
        return repositories

    def prepare_task_branches(
        self,
        repositories: list[object],
        repository_branches: dict[str, str],
    ) -> list[object]:
        self._validate_git_executable()
        for repository in repositories:
            branch_name = normalized_text(repository_branches.get(repository.id, ''))
            if not branch_name:
                raise ValueError(
                    f'missing task branch name for repository {repository.id}'
                )
            self._refuse_branching_the_source_tree(repository, branch_name)
            self._prepare_task_branch(repository, branch_name)
        return repositories

    def _refuse_branching_the_source_tree(self, repository, branch_name: str) -> None:
        """Refuse to create a task branch in the operator's SOURCE checkout.

        In workspace mode every task gets its own clone under the workspaces
        root, and branch prep is supposed to run against that clone. The
        repository objects carrying workspace paths are shallow copies —
        ``provision_task_workspace_clones`` returns the INVENTORY originals
        untouched whenever the workspace service is missing, so a single
        unwired dependency turns branch prep loose on the operator's live
        source tree instead. The symptom is not a crash: branches quietly
        appear in the folders the operator actually works in, while the task
        clone the agent is editing sits on master and never gets a PR.

        That source tree is a RUNNING system — the same reason
        ``update_source_to_task_branch`` refuses to stash. Checking out a
        branch under it can move files out from under a dev server, and it
        is never something the autonomous flow should do on its own.

        Fail-closed and BEFORE any git runs: with both roots configured and
        pointing at different trees, a path under the source root is a wiring
        bug by definition, and refusing costs one failed task while
        proceeding edits repositories kato does not own.
        """
        local_path = normalized_text(text_from_attr(repository, 'local_path'))
        source = self._source_tree_containing(local_path)
        if source is None:
            return
        raise RuntimeError(
            f'refusing to create branch {branch_name!r} in {local_path} — that '
            f'is inside the source tree ({source}), not this task\'s workspace '
            f'clone under {os.environ.get("KATO_WORKSPACES_ROOT", "")}. kato '
            f'never branches the folders you work in. The workspace clone was '
            f'not wired into branch preparation; check that '
            f'KATO_WORKSPACES_ROOT is set and the task workspace exists.'
        )

    @staticmethod
    def _source_tree_containing(local_path: str):
        """The operator's source root when ``local_path`` sits inside it.

        ``None`` when it does not, when either root is unconfigured, or when
        the two roots name the same tree — a legacy single-clone install,
        where the operator's checkout IS the working copy and everything
        below is legitimate.

        Extracted so the branch guard and the WIPE guard cannot disagree.
        They did, and in the worst possible direction: the branch guard was
        wired up while ``_make_git_ready_for_work`` was left open, so kato
        refused to CREATE A BRANCH in the operator's checkout and then ran
        ``checkout -f`` + ``reset --hard`` + ``clean -fd`` over the very same
        path. The cheap operation was guarded and the destructive one was
        not.
        """
        path = normalized_text(local_path)
        source_root = normalized_text(os.environ.get('REPOSITORY_ROOT_PATH', ''))
        workspaces_root = normalized_text(os.environ.get('KATO_WORKSPACES_ROOT', ''))
        # Both roots must be configured and distinct — see the docstring.
        if not path or not source_root or not workspaces_root:
            return None
        try:
            source = Path(source_root).expanduser().resolve()
            workspaces = Path(workspaces_root).expanduser().resolve()
            candidate = Path(path).expanduser().resolve()
        except Exception:
            return None
        if source == workspaces or candidate.is_relative_to(workspaces):
            return None
        return source if candidate.is_relative_to(source) else None

    def get_repository(self, repository_id: str):
        # ``_repositories`` is lazy-initialized via ``_ensure_repositories``
        # — iterating it directly trips on ``None`` when nothing has
        # warmed the inventory yet (e.g. the planning UI's publish-state
        # poll firing before the first scan). Use the ensure-helper so
        # the load is idempotent and the iteration is always safe.
        for repository in self._ensure_repositories():
            if repository.id == repository_id:
                return repository
        # Direct folder lookup fallback — same fast path used by
        # _resolve_repository_for_tag, so a repo resolved by tag during
        # task setup is always findable here even if the warm-up walk
        # missed it (e.g. timing, walk error, or Windows path edge case).
        direct = self._discover_repository_at_named_folder(repository_id)
        if direct is not None:
            return direct
        raise ValueError(f'unknown repository id: {repository_id}')

    def build_branch_name(self, task: Task, repository) -> str:
        return normalized_text(task.id)

    def create_pull_request(
        self,
        repository,
        title: str,
        source_branch: str,
        description: str = '',
        commit_message: str = '',
    ) -> dict[str, str]:
        return self._publication_service.create_pull_request(
            repository,
            title,
            source_branch,
            description=description,
            commit_message=commit_message,
        )

    def update_source_to_task_branch(
        self,
        repository,
        branch_name: str,
    ) -> dict[str, object]:
        """Switch the source-folder clone of ``repository`` to ``branch_name``.

        For the planning UI's "Update source" button: after a per-task
        clone has pushed its branch to the remote, the operator's
        live / running system (which lives at ``repository.local_path``
        in the inventory, NOT in the per-task workspace) needs to be
        on that branch and up-to-date so it can be tested end-to-end.

        NEVER STASHES. This used to stash → switch → pull → pop, which
        looks safe and is not: the operator's source folder is a RUNNING
        system, and the round trip rewrites their working files twice.
        A local dev config would revert for the duration of the switch and
        come back with a new mtime, so a running dev server picked up the
        committed config mid-switch and had to be restarted by hand on
        every single branch switch. Preserving the changes is not enough
        when the file itself is what a process is watching.

        Git does not need the stash here: ``git checkout`` carries
        uncommitted changes across a branch switch on its own, and only
        refuses when the switch would actually overwrite one of them.
        That refusal is rare and it is exactly the case a human should
        look at — so it is reported, not worked around.

        Steps:
          1. ``git fetch origin --prune``
          2. ``git checkout <branch_name>`` — uncommitted changes come
             along untouched. If git refuses because the switch would
             overwrite them, STOP: nothing is changed, and the blocking
             files are named in the result for the operator.
          3. ``git pull --ff-only origin <branch_name>``. Same rule: if
             the fast-forward would overwrite local changes, git refuses
             and we report which files rather than forcing it.

        Returns a status dict:
            {
              'updated': bool,   # False when a local change blocked it
              'blocked': bool,   # local changes stood in the way
              'blocking_paths': list[str],
              'carried_changes': bool,   # had local edits and kept them
              'warning': str,    # operator-readable note, may be empty
            }

        Raises ``RuntimeError`` only on truly catastrophic failures
        (missing local_path, not a git repo, fetch failed). A local
        change blocking the switch is NOT an exception — it is a normal
        answer the operator acts on.
        """
        local_path = str(getattr(repository, 'local_path', '') or '').strip()
        if not local_path:
            raise RuntimeError(
                f'repository {repository.id} has no local_path set; '
                'cannot update source folder',
            )
        if not (Path(local_path) / '.git').is_dir():
            raise RuntimeError(
                f'source folder for repository {repository.id} at '
                f'{local_path} is not a git repository',
            )
        # What the operator has in flight. Kept for the message only —
        # nothing is stashed, moved, or rewritten.
        try:
            status_output = self._working_tree_status(local_path)
        except Exception as exc:
            raise RuntimeError(
                f'failed to inspect source folder for {repository.id}: {exc}',
            ) from exc
        carried_changes = bool(status_output.strip())
        self._run_git(
            local_path,
            ['fetch', 'origin', '--prune'],
            f'failed to fetch origin for {repository.id} source folder',
        )
        # Checkout and pull are attempted WITHOUT touching the working
        # tree first. Git carries uncommitted changes across on its own and
        # refuses only when the operation would overwrite one of them —
        # which is the case a human should see, not one to force past.
        try:
            self._run_git(
                local_path,
                ['checkout', branch_name],
                f'failed to checkout branch {branch_name}',
            )
        except RuntimeError as exc:
            if not self._is_local_change_refusal(exc):
                raise
            return self._blocked_by_local_changes(
                repository, local_path, branch_name, exc, 'switching to',
            )
        try:
            self._run_git(
                local_path,
                ['pull', '--ff-only', 'origin', branch_name],
                f'failed to fast-forward {branch_name}',
            )
        except RuntimeError as exc:
            if not self._is_local_change_refusal(exc):
                raise
            return self._blocked_by_local_changes(
                repository, local_path, branch_name, exc, 'pulling',
            )
        warning = ''
        if carried_changes:
            # Said explicitly: the operator needs to know their edits are
            # still there and were never round-tripped through a stash.
            warning = (
                f'switched {repository.id} to {branch_name} and pulled; your '
                'uncommitted changes were carried across untouched (nothing '
                'was stashed)'
            )
        return {
            'updated': True,
            'blocked': False,
            'blocking_paths': [],
            'carried_changes': carried_changes,
            'warning': warning,
        }

    def _blocked_by_local_changes(
        self, repository, local_path: str, branch_name: str,
        exc: RuntimeError, action: str,
    ) -> dict:
        """Report a switch git refused, without changing anything.

        The old code stashed up front so this could not happen. It can now,
        and that is the point: it means the incoming branch genuinely
        conflicts with what the operator has open, which is a decision for
        them. Kato names the files and stops.
        """
        blocking = self._paths_from_git_refusal(str(exc))
        self.logger.warning(
            'update-source: %s %s in %s was blocked by local changes (%s)',
            action, branch_name, local_path, ', '.join(blocking) or exc,
        )
        listed = ', '.join(blocking) if blocking else 'see the detail below'
        return {
            'updated': False,
            'blocked': True,
            'blocking_paths': blocking,
            'carried_changes': True,
            'warning': (
                f'{repository.id}: kato did not finish {action} {branch_name} '
                f'because your local changes would be overwritten ({listed}). '
                f'Nothing was stashed or changed in {local_path} — your work '
                'and your current branch are exactly as you left them. '
                f'Detail: {exc}'
            ),
        }

    @staticmethod
    def _is_local_change_refusal(exc: Exception) -> bool:
        """Did git refuse specifically because of UNCOMMITTED work?

        Only that case is the operator's to decide about. A branch that
        does not exist, a diverged fast-forward, an unreachable origin —
        those are real failures and must keep raising, or removing the
        stash would quietly convert every error into a friendly "your
        local changes are in the way" that names no files and hides the
        actual problem.
        """
        detail = str(exc or '').lower()
        return any(marker in detail for marker in (
            'would be overwritten',
            'local changes to',
            'your local changes',
            'please commit your changes or stash them',
            'not uptodate',
        ))

    @staticmethod
    def _paths_from_git_refusal(detail: str) -> list[str]:
        """Pull the file list out of git's "would be overwritten" message.

        Git prints the offending paths on their own indented lines between
        the refusal and the "Please commit or stash" advice. Naming them is
        the difference between an operator knowing what to do and re-reading
        a wall of git output.
        """
        paths: list[str] = []
        collecting = False
        for line in str(detail or '').splitlines():
            lowered = line.strip().lower()
            if 'would be overwritten' in lowered or 'local changes to' in lowered:
                collecting = True
                continue
            if collecting:
                if not line.startswith((' ', '\t')) or not line.strip():
                    collecting = False
                    continue
                if lowered.startswith(('please', 'aborting', 'error', 'hint')):
                    collecting = False
                    continue
                paths.append(line.strip())
        return paths

    def publish_review_fix(
        self,
        repository,
        branch_name: str,
        commit_message: str = '',
    ) -> None:
        self._publication_service.publish_review_fix(
            repository,
            branch_name,
            commit_message=commit_message,
        )

    def list_pull_request_comments(
        self,
        repository,
        pull_request_id: str,
    ) -> list[dict[str, str]]:
        return self._publication_service.list_pull_request_comments(
            repository,
            pull_request_id,
        )

    def find_pull_requests(
        self,
        repository,
        *,
        source_branch: str = '',
        title_prefix: str = '',
    ) -> list[dict[str, str]]:
        return self._publication_service.find_pull_requests(
            repository,
            source_branch=source_branch,
            title_prefix=title_prefix,
        )

    def _resolve_branch_state(self, repository, normalized_branch: str):
        """Shared preamble for the branch gates: validate + read HEAD.

        Returns ``(local_path, current_branch)`` once the workspace clone
        is locatable and its HEAD branch is readable, or ``None`` on any
        failure (empty path/branch, missing ``.git``, or a ``_current_branch``
        error). Callers map ``None`` to their own on-failure default —
        ``branch_needs_push`` to ``False`` (don't promise a push), and
        ``workspace_has_task_changes`` to ``True`` (fall through to the
        update path). They also keep their own ``current_branch !=
        normalized_branch`` handling and divergent tails.
        """
        local_path = str(getattr(repository, 'local_path', '') or '').strip()
        if not local_path or not normalized_branch:
            return None
        try:
            if not (Path(local_path) / '.git').is_dir():
                return None
        except OSError:
            return None
        try:
            current_branch = self._current_branch(local_path)
        except Exception:
            return None
        return local_path, current_branch

    def branch_needs_push(self, repository, branch_name: str) -> bool:
        """True when ``Push`` would actually publish something.

        The boolean face of :meth:`push_skip_reason` — an empty reason
        means the push would do real work.
        """
        return not self.push_skip_reason(repository, branch_name)

    def pull_request_skip_reason(self, repository, branch_name: str) -> str:
        """Why a pull request here would have nothing in it — ``''`` to open one.

        Opening a PR whose branch matches its destination produces an EMPTY
        pull request: no files, no diff, nothing to review. The operator has
        to notice and decline it, once per repo, every time.

        Two ways a task branch ends up with nothing to show, both reported:

        * the agent made a change and then reverted it, so the branch's
          commits cancel out;
        * a multi-repo task where one repo has the fix and the others were
          already merged — publishing re-opens a PR for every repo.

        The test is the DIFF against the destination, not the commit count:
        a revert leaves commits behind but no net change, and a commit-count
        check would call that publishable.

        Compared against the REMOTE destination (``origin/<branch>``) where
        possible — the local copy of it can be many commits stale, which
        would make an already-merged branch look like it still has work.

        Best-effort in the SAFE direction: any git failure returns ``''`` so
        the PR is still attempted. A missed empty PR is an annoyance; a
        suppressed real one loses work.
        """
        normalized_branch = (branch_name or '').strip()
        if not normalized_branch:
            return ''
        # Deliberately NOT ``_resolve_branch_state``: that also reads which
        # branch is checked out, which this comparison does not care about —
        # the refs are compared directly. Depending on it would make the
        # guard silently pass (publish) for a clone sitting on another
        # branch, which is one of the states that produces an empty PR.
        local_path = str(getattr(repository, 'local_path', '') or '').strip()
        if not local_path:
            return ''
        try:
            if not (Path(local_path) / '.git').is_dir():
                return ''
        except OSError:
            return ''
        try:
            destination = self.destination_branch(repository)
        except Exception:
            return ''
        if not destination or destination == normalized_branch:
            return ''
        base = self._pull_request_base_ref(local_path, destination)
        if not base:
            return ''
        try:
            # ``--quiet`` exits 1 when there IS a difference. Three dots so
            # the comparison is against the merge base: destination commits
            # the branch has not merged are not the branch's changes.
            self._run_git(
                local_path,
                ['diff', '--quiet', f'{base}...{normalized_branch}'],
                'diff check failed',
                repository,
            )
        except Exception:
            # Non-zero exit — there IS a diff, which is the publishable case.
            return ''
        return (
            f'no changes on {normalized_branch!r} compared with '
            f'{base!r} — a pull request would be empty'
        )

    def _pull_request_base_ref(self, local_path: str, destination: str) -> str:
        """``origin/<destination>`` when it exists, else the local branch."""
        remote_ref = f'origin/{destination}'
        try:
            self._run_git(
                local_path,
                ['rev-parse', '--verify', '--quiet', remote_ref],
                'remote ref check failed',
                None,
            )
            return remote_ref
        except Exception:
            pass
        try:
            self._run_git(
                local_path,
                ['rev-parse', '--verify', '--quiet', destination],
                'local ref check failed',
                None,
            )
            return destination
        except Exception:
            return ''

    def recover_clone_onto_task_branch(self, repository, branch_name: str) -> str:
        """Move a clone that never got branch-prepped onto its task branch.

        The classic mid-task repo: added after the task started, so nothing
        ever put its clone on the task branch. The agent then works on
        ``master``, and every push reports "nothing to push" — accurate, and
        a dead end. The operator's changes are sitting right there, and the
        only thing standing between them and a pull request is a checkout.

        Attempted ONLY when it is provably safe: the clone must have no
        commits of its own on the wrong branch. Uncommitted work is fine and
        is the normal case — the checkout below carries a dirty tree across
        with it, so the changes arrive on the task branch intact.

        The uncommitted tree is the WHOLE POINT of this method: a clone that
        was never branch-prepped has the agent's entire output sitting in it
        unstaged. The first version of this routed through
        ``_prepare_task_branch`` on the reasoning that it is "the" way to get
        onto a task branch. It is not — it is the START-OF-TASK path, and on
        a dirty tree it wipes to ``origin/<destination>`` without a stash.
        That shipped and destroyed 41 files of real work. Recovery must use
        ``_checkout_task_branch_preserving_worktree`` and nothing else.

        A clone that DOES have commits on the wrong branch is left alone and
        reported. Those commits would stay behind on that branch, and moving
        them is a rebase-or-cherry-pick decision with a real chance of losing
        work — not something to do silently on the operator's behalf while
        they are looking at a button that says "push".

        Returns '' on success, or a reason describing why it was not done.
        """
        normalized_branch = normalized_text(branch_name)
        if not normalized_branch:
            return 'no task branch name'
        state = self._resolve_branch_state(repository, normalized_branch)
        if state is None:
            return 'workspace clone is missing or its branch is unreadable'
        local_path, current_branch = state
        if current_branch == normalized_branch:
            return ''
        # Same refusal ``prepare_task_branches`` makes, repeated here because
        # this method creates a branch WITHOUT going through it — a caller
        # holding an inventory object would otherwise branch the operator's
        # own checkout, which is exactly what happened the first time this
        # recovery was wired into the sync path.
        try:
            self._refuse_branching_the_source_tree(
                SimpleNamespace(id=repository.id, local_path=local_path),
                normalized_branch,
            )
        except RuntimeError as exc:
            return str(exc)
        try:
            destination_branch = self.destination_branch(repository)
            reference = self._comparison_reference(local_path, destination_branch)
        except Exception:
            return 'could not resolve the destination branch to compare against'
        try:
            # Same helper the push pre-check uses, so "has its own commits"
            # means the same thing in both places.
            own_commits = self._ahead_count(local_path, reference, current_branch)
        except Exception:
            return 'could not read the clone\'s commit history'
        if own_commits:
            return (
                f'clone is on {current_branch!r} and has {own_commits} '
                f'commit(s) of its own there. Moving them to '
                f'{normalized_branch!r} is a rebase or cherry-pick, which '
                f'kato will not do for you — do it in the clone, then push.'
            )
        try:
            self._checkout_task_branch_preserving_worktree(
                repository,
                local_path,
                normalized_branch,
            )
        except Exception as exc:
            return f'could not move the clone onto {normalized_branch!r}: {exc}'
        return ''

    def _checkout_task_branch_preserving_worktree(
        self,
        repository,
        local_path: str,
        branch_name: str,
    ) -> None:
        """Move onto ``branch_name`` carrying any uncommitted work with it.

        Deliberately NOT ``_prepare_task_branch``. That is the start-of-task
        cleanup path: when the tree is dirty it calls
        ``_make_git_ready_for_work``, which runs ``checkout -f`` →
        ``reset --hard origin/<destination>`` → ``clean -fd`` and keeps NO
        stash of what it removed. Against a fresh clone that is correct and
        intended. Against a clone holding the agent's uncommitted output it
        deletes precisely the work the recovery exists to rescue.

        No ``-f``, no ``reset``, no ``clean`` here. A plain checkout carries
        modified files onto the new branch, and in the rare case where it
        could not, git REFUSES instead of overwriting — that refusal becomes
        the caller's reason string. The failure mode is "nothing happened",
        never "your changes are gone".
        """
        existing = self._git_stdout(
            local_path,
            ['branch', '--list', branch_name],
            f'failed to list local branches at {local_path}',
            repository,
        )
        self._run_git(
            local_path,
            ['checkout', branch_name] if existing else ['checkout', '-b', branch_name],
            f'failed to move repository at {local_path} onto {branch_name}',
            repository,
        )

    def push_skip_reason(self, repository, branch_name: str) -> str:
        """Why ``Push`` would do nothing here — ``''`` when it would push.

        The on-demand push path (``publish_review_fix``) refuses to
        proceed unless three preconditions hold; this check mirrors all
        three so the planning UI doesn't enable a button whose click
        would error:

        1. The workspace is currently checked out on ``branch_name`` —
           ``_assert_branch_checked_out`` rejects everything else.
        2. There would be at least one commit ahead of the destination
           branch after committing any dirty tree —
           ``_ensure_branch_is_publishable`` raises
           ``RepositoryHasNoChangesError`` when the branch is in sync.
        3. The push would actually send work to the remote — ``origin/
           <branch>`` is missing or behind, OR the working tree is dirty
           (the new commit will move local past origin).

        It returns the REASON rather than a bare False because every
        one of these failures used to reach the operator as the same
        "nothing to push" line. A repo whose clone never got moved onto
        the task branch (the classic symptom of a repo added mid-task)
        is indistinguishable, in that wording, from a repo that is
        genuinely in sync — so the work looked pushed when it was
        sitting untouched on master. Callers surface this text.

        Best-effort: any git failure yields a reason (never a push
        promise), so the button stays disabled rather than promising a
        push that won't work.
        """
        normalized_branch = (branch_name or '').strip()
        if not normalized_branch:
            return 'no task branch name'
        state = self._resolve_branch_state(repository, normalized_branch)
        if state is None:
            return 'workspace clone is missing or its branch is unreadable'
        local_path, current_branch = state
        # Precondition 1 — publish_review_fix asserts the workspace is
        # checked out on the task branch. If it isn't (e.g. workspace
        # was reset to master after a prior publish, or the clone was
        # added mid-task and never branch-prepped), there's nothing the
        # Push button can do without first checking out.
        if current_branch != normalized_branch:
            return (
                f'clone is on {current_branch!r}, not the task branch '
                f'{normalized_branch!r} — nothing was committed to the '
                f'task branch, so there is nothing to push'
            )
        try:
            is_dirty = bool(self._working_tree_status(local_path).strip())
        except Exception:
            return 'could not read the working tree'
        # Precondition 2 — branch must be (or become, after committing
        # dirty tree) ahead of the destination branch.
        try:
            destination_branch = self.destination_branch(repository)
            comparison_reference = self._comparison_reference(
                local_path, destination_branch,
            )
        except Exception:
            return 'could not determine the destination branch'
        try:
            ahead_destination = self._ahead_count(
                local_path, comparison_reference, normalized_branch,
            )
        except Exception:
            return 'could not compare the task branch with its destination'
        if ahead_destination == 0 and not is_dirty:
            return 'nothing to push — no commits on the task branch and a clean tree'
        # Precondition 3 — push must send something the remote doesn't
        # already have. Dirty tree → upcoming commit will exceed origin.
        if is_dirty:
            return ''
        remote_reference = f'origin/{normalized_branch}'
        try:
            remote_branch_exists = self._git_reference_exists(
                local_path, remote_reference,
            )
        except Exception:
            return 'could not read the remote branch'
        if not remote_branch_exists:
            return ''
        try:
            ahead_remote, _behind = self._left_right_commit_counts(
                local_path, normalized_branch, remote_reference,
            )
        except Exception:
            return 'could not compare the task branch with origin'
        if ahead_remote > 0:
            return ''
        return 'nothing to push — origin already has every commit'

    def workspace_has_task_changes(self, repository, branch_name: str) -> bool:
        """True when the workspace clone has commits on the task branch.

        Drives the "Update source" skip path: ``update_source_to_task_branch``
        only does fetch / checkout / pull — it never commits. So the only
        thing that can propagate from the workspace to the operator's
        source folder is a commit that already lives on the task branch.
        Untracked artifacts left behind by the agent's test runs (npm
        install caches, .pytest_cache, build outputs, etc.) are
        deliberately NOT a "change" signal: counting them would falsely
        flip every repo to "changed" and pull the operator off their
        current branch for nothing.

        Returns ``True`` on any inspection failure so unexpected git
        states fall through to the update path rather than silently
        swallow real work the operator was expecting.

        Skip rules (return ``False``):
        - Workspace HEAD is not on the task branch — the agent never
          moved off master, so there is nothing branch-shaped to ship.
        - Workspace IS on the task branch but has zero commits ahead of
          the destination branch — branch exists but is empty of work.
        """
        normalized_branch = (branch_name or '').strip()
        state = self._resolve_branch_state(repository, normalized_branch)
        if state is None:
            return True
        local_path, current_branch = state
        if current_branch != normalized_branch:
            return False
        try:
            destination_branch = self.destination_branch(repository)
            comparison_reference = self._comparison_reference(
                local_path, destination_branch,
            )
            ahead = self._ahead_count(
                local_path, comparison_reference, normalized_branch,
            )
        except Exception:
            return True
        return ahead > 0

    def pull_workspace_clone(
        self,
        repository,
        branch_name: str,
    ) -> dict[str, object]:
        """Fast-forward the per-task workspace clone of ``repository`` to
        ``origin/<branch_name>``.

        Operator-driven. Drives the planning UI's ``Pull`` button —
        symmetric to ``Push``. Refuses cleanly (does NOT auto-stash)
        when the working tree is dirty: pulling would risk colliding
        with in-progress agent edits, and the safer move is to let
        the operator commit / discard those first. ``update_source``
        is the place where we DO auto-stash, because it's targeting
        the operator's own checkout, not kato's working clone.

        Returns a status dict with one of:
            {'pulled': True,  'updated': bool, 'commits_pulled': int}
            {'pulled': False, 'reason': '<short>', 'detail': '<long>'}
        """
        local_path = str(getattr(repository, 'local_path', '') or '').strip()
        normalized_branch = (branch_name or '').strip()
        if not local_path:
            return {
                'pulled': False, 'reason': 'no_local_path',
                'detail': f'repository {repository.id} has no local_path set',
            }
        if not (Path(local_path) / '.git').is_dir():
            return {
                'pulled': False, 'reason': 'not_a_git_repo',
                'detail': f'workspace clone for {repository.id} at '
                          f'{local_path} is not a git repository',
            }
        if not normalized_branch:
            return {
                'pulled': False, 'reason': 'no_branch',
                'detail': f'no task branch for {repository.id}',
            }
        try:
            current = self._current_branch(local_path)
        except Exception as exc:
            return {
                'pulled': False, 'reason': 'branch_lookup_failed',
                'detail': str(exc),
            }
        # The branch the workspace is on must be the task branch we
        # are pulling into; otherwise a fast-forward would land in
        # the wrong place. Operator-fixable (checkout the task
        # branch first), so we surface a clear reason.
        if current != normalized_branch:
            return {
                'pulled': False, 'reason': 'wrong_branch_checked_out',
                'detail': f'workspace is on {current!r}, expected '
                          f'{normalized_branch!r} — checkout first',
            }
        try:
            dirty = bool(self._working_tree_status(local_path).strip())
        except Exception as exc:
            return {
                'pulled': False, 'reason': 'status_check_failed',
                'detail': str(exc),
            }
        if dirty:
            return {
                'pulled': False, 'reason': 'dirty_working_tree',
                'detail': 'workspace has uncommitted changes; commit or '
                          'discard them before pulling',
            }
        try:
            self._run_git(
                local_path, ['fetch', 'origin', '--prune'],
                f'failed to fetch origin for {repository.id} workspace',
                repository,
            )
        except RuntimeError as exc:
            return {'pulled': False, 'reason': 'fetch_failed', 'detail': str(exc)}
        remote_reference = f'origin/{normalized_branch}'
        try:
            remote_exists = self._git_reference_exists(local_path, remote_reference)
        except Exception as exc:
            return {
                'pulled': False, 'reason': 'remote_lookup_failed',
                'detail': str(exc),
            }
        if not remote_exists:
            # No remote branch to pull from. Common right after a
            # fresh task before anything was pushed; not an error,
            # just a no-op.
            return {
                'pulled': True, 'updated': False, 'commits_pulled': 0,
                'reason': 'remote_branch_missing',
            }
        try:
            _ahead, behind = self._left_right_commit_counts(
                local_path, normalized_branch, remote_reference,
            )
        except Exception as exc:
            return {
                'pulled': False, 'reason': 'commit_count_failed',
                'detail': str(exc),
            }
        if behind == 0:
            return {'pulled': True, 'updated': False, 'commits_pulled': 0}
        try:
            self._run_git(
                local_path,
                ['pull', '--ff-only', 'origin', normalized_branch],
                f'failed to fast-forward {repository.id} workspace from origin',
                repository,
            )
        except RuntimeError as exc:
            return {'pulled': False, 'reason': 'pull_failed', 'detail': str(exc)}
        return {'pulled': True, 'updated': True, 'commits_pulled': int(behind)}

    def _merge_preflight(
        self,
        repository,
        local_path: str,
        normalized_branch: str,
    ) -> dict[str, object]:
        """Validate the clone is safe to merge into.

        Returns ``{'error': <status dict>}`` on any refusal, or
        ``{'default_branch': <name>}`` when the clone is on the task
        branch with a clean tree and the default branch is known.
        """
        def fail(reason: str, detail: str) -> dict[str, object]:
            return {'error': {
                'merged': False, 'reason': reason, 'detail': detail,
            }}

        if not local_path:
            return fail(
                'no_local_path',
                f'repository {repository.id} has no local_path set',
            )
        if not (Path(local_path) / '.git').is_dir():
            return fail(
                'not_a_git_repo',
                f'workspace clone for {repository.id} at {local_path} '
                f'is not a git repository',
            )
        if not normalized_branch:
            return fail('no_branch', f'no task branch for {repository.id}')
        try:
            current = self._current_branch(local_path)
        except Exception as exc:
            return fail('branch_lookup_failed', str(exc))
        if current != normalized_branch:
            return fail(
                'wrong_branch_checked_out',
                f'workspace is on {current!r}, expected '
                f'{normalized_branch!r} — checkout first',
            )
        try:
            return {'default_branch': self.destination_branch(repository)}
        except ValueError as exc:
            return fail('default_branch_unknown', str(exc))

    def merge_default_branch_into_clone(
        self,
        repository,
        branch_name: str,
    ) -> dict[str, object]:
        """Fetch + merge the repo's default branch into the task branch.

        Drives the planning UI's ``Merge master`` button. The agent's
        per-task clone is intentionally blocked from running git, so
        when the task branch falls behind ``origin/<default>`` and
        develops conflicts the agent has no way to pull + merge
        itself. This does the git plumbing on the operator's behalf
        and — crucially — when the merge conflicts it does NOT abort:
        the conflict markers + ``MERGE_HEAD`` are left in the working
        tree so the agent can resolve them by editing files, and
        kato's normal commit/push flow finalises the merge.

        Returns one of:
            {'merged': True,  'updated': bool, 'default_branch': str,
             'commits_merged': int}
            {'merged': False, 'conflicts': True, 'default_branch': str,
             'conflicted_files': [str, ...]}
            {'merged': False, 'reason': '<short>', 'detail': '<long>'}
        """
        local_path = str(getattr(repository, 'local_path', '') or '').strip()
        normalized_branch = (branch_name or '').strip()
        preflight = self._merge_preflight(
            repository, local_path, normalized_branch,
        )
        if preflight.get('error'):
            return preflight['error']
        default_branch = preflight['default_branch']
        # The agent's in-progress edits would make the merge git-unsafe —
        # but refusing here turned every "Merge master" click into a chore
        # (and the UI misread the refusal as "already up to date"). Save
        # the work as a WIP commit on the task branch instead: kato owns
        # the git plumbing, the later push includes it, and a conflicted
        # merge can no longer tangle uncommitted files.
        wip_committed = False
        try:
            status_output = self._working_tree_status(local_path)
            dirty = bool(status_output.strip())
        except Exception as exc:
            return {
                'merged': False, 'reason': 'status_check_failed',
                'detail': str(exc),
            }
        if dirty:
            try:
                self._run_git(
                    local_path, ['add', '-A'],
                    f'failed to stage in-progress work for {repository.id}',
                    repository,
                )
                # Kato's publication exclusions have to hold HERE too. A
                # blanket ``add -A`` used to sweep the agent's
                # ``validation_report.md`` into this commit — and once the
                # report is TRACKED, the publish path can never strip it
                # again: its ``reset``+``clean`` only reach files that are
                # untracked or merely staged. That is exactly how one report
                # rode three "WIP: save in-progress work" commits onto a task
                # branch and into the PR, growing on every later run.
                #
                # Unstage rather than delete: the agent may still be writing
                # the report, and publication CONSUMES it (it becomes the PR
                # description, then the file is cleaned). Leaving it dirty in
                # the tree is harmless for the merge — these paths don't exist
                # on the default branch, so they cannot conflict.
                excluded = self._unstage_publication_excluded_paths(
                    local_path, status_output, repository,
                )
                # Only worth a round-trip when something actually left the
                # index; otherwise a dirty tree plus ``add -A`` guarantees
                # there is something to commit.
                if excluded and not self._staged_paths(local_path):
                    # Everything dirty was excluded — there is nothing to
                    # save, and ``git commit`` would fail on an empty index.
                    raise _NothingToCommit
                self._run_git(
                    local_path,
                    ['commit', '-m',
                     f'WIP: save in-progress work before merging '
                     f'{default_branch} (kato)'],
                    f'failed to save in-progress work for {repository.id}',
                    repository,
                )
            except _NothingToCommit:
                wip_committed = False
            except RuntimeError as exc:
                return {
                    'merged': False, 'reason': 'wip_commit_failed',
                    'detail': str(exc),
                }
            else:
                wip_committed = True
        try:
            self._run_git(
                local_path, ['fetch', 'origin', '--prune'],
                f'failed to fetch origin for {repository.id} workspace',
                repository,
            )
        except RuntimeError as exc:
            return {'merged': False, 'reason': 'fetch_failed', 'detail': str(exc)}
        remote_reference = f'origin/{default_branch}'
        try:
            remote_exists = self._git_reference_exists(
                local_path, remote_reference,
            )
        except Exception as exc:
            return {
                'merged': False, 'reason': 'remote_lookup_failed',
                'detail': str(exc),
            }
        if not remote_exists:
            return {
                'merged': False, 'reason': 'remote_default_missing',
                'detail': f'{remote_reference} does not exist on origin',
            }
        try:
            _ahead, behind = self._left_right_commit_counts(
                local_path, normalized_branch, remote_reference,
            )
        except Exception as exc:
            return {
                'merged': False, 'reason': 'commit_count_failed',
                'detail': str(exc),
            }
        if behind == 0:
            # Task branch already contains every commit from the
            # default branch — nothing to merge.
            return {
                'merged': True, 'updated': False, 'commits_merged': 0,
                'default_branch': default_branch,
                'wip_committed': wip_committed,
            }
        # ``_run_git_subprocess`` (not ``_run_git``) — a merge
        # conflict is a non-zero exit we EXPECT and want to handle,
        # not raise on.
        merge_result = self._run_git_subprocess(
            local_path,
            ['merge', '--no-edit', remote_reference],
            repository,
        )
        if merge_result.returncode == 0:
            return {
                'merged': True, 'updated': True,
                'commits_merged': int(behind),
                'default_branch': default_branch,
                'wip_committed': wip_committed,
            }
        conflicted = self._unmerged_paths(local_path)
        if conflicted:
            # Leave the conflict markers + MERGE_HEAD in place — the
            # agent resolves them by editing files; kato's normal
            # commit/push finalises the merge.
            return {
                'merged': False, 'conflicts': True,
                'default_branch': default_branch,
                'conflicted_files': conflicted,
            }
        # Non-zero exit but no unmerged paths → some other merge
        # failure (e.g. refusing for an unrelated reason). Abort so
        # the tree is left clean rather than half-merged.
        self._run_git_subprocess(local_path, ['merge', '--abort'], repository)
        detail = (
            merge_result.stderr.strip()
            or merge_result.stdout.strip()
            or 'git merge failed'
        )
        return {'merged': False, 'reason': 'merge_failed', 'detail': detail}

    def _unmerged_paths(self, local_path: str) -> list[str]:
        """Repo-relative paths with conflict (unmerged) index entries."""
        result = self._run_git_subprocess(
            local_path,
            ['diff', '--no-ext-diff', '--name-only', '--diff-filter=U'],
        )
        if result.returncode != 0:
            return []
        return [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]

    def _merge_in_progress(self, local_path: str) -> bool:
        """True when a merge started in this clone is still pending.

        ``MERGE_HEAD`` exists between ``git merge`` conflicting and the
        finalising commit — it's the marker that ``merge_default_branch_into_clone``
        left the tree mid-merge for the agent to resolve.
        """
        result = self._run_git_subprocess(
            local_path, ['rev-parse', '-q', '--verify', 'MERGE_HEAD'],
        )
        return result.returncode == 0

    def _file_still_conflicted(self, local_path: str, rel_path: str) -> bool:
        """True when the working-tree file still carries git conflict markers.

        The agent resolves a conflict by EDITING the file to remove the
        ``<<<<<<<`` / ``>>>>>>>`` markers — it can't ``git add`` (sandbox),
        so the index keeps the path unmerged and ``git ls-files --unmerged``
        can't tell "resolved but unstaged" from "still conflicted". The
        working-tree markers are the honest signal. We key on the opening
        and closing markers (7 chars + a space) — those effectively never
        occur in real source, so no false "still conflicted".
        """
        full = os.path.join(local_path, rel_path)
        try:
            with open(full, 'r', encoding='utf-8', errors='replace') as handle:
                for line in handle:
                    if line.startswith(('<<<<<<< ', '>>>>>>> ')):
                        return True
        except OSError:
            # File removed as part of the resolution (delete/modify
            # conflict resolved by deletion) — nothing left to conflict.
            return False
        return False

    def finalize_merge_if_resolved(self, repository) -> dict[str, object]:
        """Commit a pending merge once the agent has resolved its conflicts.

        ``merge_default_branch_into_clone`` deliberately leaves ``MERGE_HEAD``
        + conflict markers in the tree so the agent can resolve them by
        editing files. The agent can't run git, so without this the merge
        never completes: the index keeps its unmerged entries and the
        working tree carries every merged-in change — so the Changes/Files
        diff shows ALL of the default branch's changes, not just the task
        branch's (the "hard to track" bug). This finalises the merge —
        the completion of the operator's own "Merge master" click — but
        ONLY when every conflicted file's markers are gone, so a half-done
        resolution is never committed.

        Returns:
            {'finalized': True,  'repository_id': str, 'default_branch': str}
            {'finalized': False, 'reason': '<short>', ...}
        """
        local_path = str(getattr(repository, 'local_path', '') or '').strip()
        if not local_path:
            return {'finalized': False, 'reason': 'no_local_path'}
        with self._merge_finalize_lock:
            if not self._merge_in_progress(local_path):
                return {'finalized': False, 'reason': 'no_pending_merge'}
            unresolved = [
                path for path in self._unmerged_paths(local_path)
                if self._file_still_conflicted(local_path, path)
            ]
            if unresolved:
                return {
                    'finalized': False, 'reason': 'conflicts_remain',
                    'unresolved_files': unresolved,
                }
            try:
                # ``add -A`` stages the resolved files. The pre-merge WIP
                # commit means the only uncommitted work here IS the merge
                # resolution, so this never sweeps up unrelated edits.
                self._run_git(
                    local_path, ['add', '-A'],
                    f'failed to stage resolved merge for {repository.id}',
                    repository,
                )
                # ``--no-edit`` keeps git's prepared MERGE_MSG → a proper
                # merge commit.
                self._run_git(
                    local_path, ['commit', '--no-edit'],
                    f'failed to finalize merge for {repository.id}',
                    repository,
                )
            except RuntimeError as exc:
                return {
                    'finalized': False, 'reason': 'commit_failed',
                    'detail': str(exc),
                }
            return {'finalized': True, 'repository_id': repository.id}

    def resolve_review_comment(self, repository, comment) -> None:
        self._publication_service.resolve_review_comment(repository, comment)

    def reply_to_review_comment(self, repository, comment, body: str) -> None:
        self._publication_service.reply_to_review_comment(repository, comment, body)

    def destination_branch(self, repository) -> str:
        configured_branch = text_from_attr(repository, 'destination_branch')
        if configured_branch:
            return configured_branch
        self._validate_local_path(repository)
        try:
            inferred_branch = self._infer_default_branch(repository.local_path)
        except ValueError as exc:
            raise ValueError(
                f'unable to determine destination branch for repository {repository.id}'
            ) from exc
        if not inferred_branch:
            raise ValueError(
                f'unable to determine destination branch for repository {repository.id}'
            )
        return inferred_branch

    def is_branch_pushable(self, repository, branch_name: str) -> bool:
        """True when kato can push ``branch_name`` to ``repository``'s remote.

        Non-raising counterpart of ``_ensure_branch_is_pushable``. Used by the
        preflight to PARTITION repos into writable vs read-only (reference): a
        repo kato lacks push rights to (e.g. an external library returning a
        403) is marked read-only instead of rejecting the whole task.
        """
        try:
            self._ensure_branch_is_pushable(
                text_from_attr(repository, 'local_path'),
                branch_name,
                repository,
            )
            return True
        except Exception:
            return False

    def _ensure_branch_is_pushable(
        self,
        local_path: str,
        branch_name: str,
        repository=None,
    ) -> None:
        try:
            self._push_branch(local_path, branch_name, repository, dry_run=True)
        except RuntimeError as exc:
            error_text = str(exc)
            error_detail = error_text.split(': ', 1)[1] if ': ' in error_text else error_text
            if (
                'could not read Password' in error_text
                or 'terminal prompts disabled' in error_text
                or 'Authentication failed' in error_text
                or 'credentials lack one or more required privilege scopes' in error_text
            ):
                raise RuntimeError(
                    f'[Error] {local_path} missing git push permissions. cannot work. '
                    f'{error_detail}'
                ) from None
            raise RuntimeError(
                f'[Error] {local_path} git push validation failed. {error_detail}'
            ) from None

    def _prepare_task_repository(self, repository):
        self._prepare_repository_access(repository)
        setattr(repository, 'destination_branch', self.destination_branch(repository))
        # A per-task workspace clone is NOT put back on the destination
        # branch here.
        #
        # ``_prepare_workspace_for_task`` exists for the shared checkout of a
        # legacy single-clone install: return it to master, ready for the
        # next task. Against a per-task clone it is actively wrong, and it
        # ran as preflight STEP 5 while the task branch was only created at
        # STEP 8. Anything returning in between — a task whose description is
        # too thin to act on, one repo raising while the earlier ones had
        # already been processed — left the entire workspace sitting on
        # master with no task branch anywhere. That is the report exactly:
        # "he will clone all the repos but will not create the branch by the
        # task name in them, all the repos will sit on master."
        #
        # ``_publish_branch_updates`` already refuses this for the same
        # reason (per-task clones "must STAY on the task branch across
        # publish operations"); preflight simply never got the same guard.
        if _is_per_task_workspace_clone(repository):
            return repository
        self._prepare_workspace_for_task(
            repository.local_path,
            repository.destination_branch,
            repository,
        )
        return repository

    def _stash_before_forced_restore(self, repository) -> None:
        """Park the working tree so a forced restore can be undone.

        ``git stash push -u`` keeps untracked files too — ``clean -fd`` is
        part of what follows, so leaving them out would preserve half the
        work and delete the rest.

        The stash is left ON the stash list deliberately. Nothing pops it:
        the task has just failed, and silently reapplying its half-finished
        output to a branch the operator is about to look at would be its own
        kind of damage. It sits in ``git stash list`` with the task branch in
        its message, and ``git stash apply`` brings it back.

        Raises if the stash fails, which aborts the restore before anything
        destructive runs. A repo left on the task branch with its work intact
        is a far better outcome than a tidy branch and no work.

        The stale-index-lock retry is why this is not a bare ``_run_git``.
        That helper recovers from a stale lock only when git names it —
        ``_is_git_index_lock_error`` wants both "index.lock" and "file
        exists" in the output — and ``git stash`` under a stale lock says
        only ``error: could not write index``. Without the retry, a lock
        left behind by a killed git process (the case
        ``test_restore_task_repositories_recovers_from_stale_git_index_lock``
        exists for) would turn a fully recoverable restore into a hard
        abort, and fail-closed would have made kato LESS able to finish.
        """
        args = [
            'stash', 'push', '--include-untracked', '-m',
            f'kato: work in progress before forced restore of {repository.id}',
        ]
        message = (
            f'failed to stash work in progress for {repository.id} before '
            f'restoring it; refusing to discard the changes'
        )
        try:
            self._run_git(repository.local_path, args, message, repository)
            return
        except Exception:
            if not self._clear_stale_git_index_lock(repository.local_path):
                raise
        self._run_git(repository.local_path, args, message, repository)

    def _restore_task_repository(self, repository, force: bool = False) -> None:
        # A per-task workspace clone is never "restored" to the destination
        # branch. It belongs to one task and must STAY on that task's branch
        # — the same rule ``_publish_branch_updates`` and
        # ``_restore_workspace_after_publication`` already apply. This was
        # the one restore site of the four that did not, and it is the one
        # every task FAILURE goes through
        # (``task_failure_handler`` → ``restore_task_repositories(force=True)``
        # over ``prepared_task.repositories``, which are the workspace
        # clones).
        #
        # The result was the operator's report exactly: a task that fails for
        # any reason ends with every repo back on master, and the ones the
        # agent had worked in emptied. It never healed either — each 180s
        # tick re-ran the failure and added another stash, leaving the
        # clones on master forever.
        #
        # Note this also covers CLEAN clones. The early return below needs
        # ``current_branch == destination_branch``, which a clone on its task
        # branch never satisfies, so an untouched repo was moved to master
        # too — dragged there by a sibling repo's failure.
        if _is_per_task_workspace_clone(repository):
            return
        local_path = text_from_attr(repository, 'local_path')
        if local_path and not (Path(local_path) / '.git').is_dir():
            self.logger.info(
                'skipping repository restore for %s because %s is no longer a git checkout',
                repository.id,
                local_path,
            )
            return
        self._validate_local_path(repository)
        destination_branch = text_from_attr(repository, 'destination_branch') or self.destination_branch(
            repository
        )
        current_branch = self._current_branch(repository.local_path)
        dirty_worktree = bool(self._working_tree_status(repository.local_path))
        if current_branch == destination_branch and not dirty_worktree:
            return
        if dirty_worktree and not force:
            self.logger.warning(
                'skipping repository restore for %s because the worktree is dirty on branch %s',
                repository.id,
                current_branch or '<unknown>',
            )
            return
        if dirty_worktree and force:
            self.logger.warning(
                'forcing repository restore for %s to branch %s despite dirty worktree on branch %s',
                repository.id,
                destination_branch,
                current_branch or '<unknown>',
            )
        try:
            if dirty_worktree and force:
                # Stash FIRST. What follows is checkout -f → reset --hard →
                # clean -fd with no safety net of its own, and on this path
                # the dirty tree is the agent's entire output for the task.
                # A failed task therefore used to end with every repo the
                # agent had touched back on the destination branch and empty
                # — "all the repos will sit on master and he will just delete
                # the entire code from some repos". ("Some" because a repo
                # the agent never modified is clean and returns early above.)
                #
                # Refuses to continue if the stash does not take: a restore
                # that cannot be undone is not worth a tidy branch state.
                self._stash_before_forced_restore(repository)
                self._make_git_ready_for_work(
                    repository.local_path,
                    destination_branch,
                    repository,
                )
            else:
                self._run_git(
                    repository.local_path,
                    ['checkout', destination_branch],
                    f'failed to restore repository at {repository.local_path} to {destination_branch}',
                    repository,
                )
            self.logger.info(
                'restored repository at %s to branch %s after task rejection',
                repository.local_path,
                destination_branch,
            )
        except Exception as exc:
            self.logger.warning(
                'failed to restore repository %s to %s after task rejection: %s',
                repository.id,
                destination_branch,
                exc,
            )

    def _prepare_task_branch(self, repository, branch_name: str):
        self._validate_local_path(repository)
        destination_branch = text_from_attr(
            repository,
            'destination_branch',
        ) or self.destination_branch(repository)
        setattr(repository, 'destination_branch', destination_branch)
        self._prepare_workspace_for_branch(
            repository.local_path,
            destination_branch,
            branch_name,
            repository,
        )
        return repository

    def _publish_repository_branch(
        self,
        repository,
        branch_name: str,
        *,
        commit_message: str,
        default_commit_message: str,
    ) -> str:
        self._validate_local_path(repository)
        destination_branch = self.destination_branch(repository)
        self._publish_branch_updates(
            repository.local_path,
            branch_name,
            destination_branch,
            normalized_text(commit_message) or default_commit_message,
            repository,
        )
        return destination_branch

    def _prepare_branch_for_publication(
        self,
        local_path: str,
        branch_name: str,
        destination_branch: str,
        commit_message: str,
    ) -> str:
        self._assert_branch_checked_out(local_path, branch_name)
        validation_report_description = self._commit_branch_changes_if_needed(
            local_path,
            branch_name,
            commit_message,
        )
        self._ensure_branch_is_publishable(
            local_path,
            branch_name,
            destination_branch,
        )
        return validation_report_description

    def _assert_branch_checked_out(self, local_path: str, branch_name: str) -> None:
        current_branch = self._current_branch(local_path)
        if current_branch == branch_name:
            return
        raise RuntimeError(
            f'expected repository at {local_path} to be on branch {branch_name}, '
            f'but found {current_branch or "<unknown>"}'
        )

    def _commit_branch_changes_if_needed(
        self,
        local_path: str,
        branch_name: str,
        commit_message: str,
    ) -> str:
        # The description now lives in the TASK folder, beside the clones
        # rather than inside one, so it is readable whether or not this repo
        # had changes — read it before the early return.
        task_folder_description = self._pr_description_from_task_folder(local_path)
        status_output = self._working_tree_status(local_path)
        if not status_output:
            return task_folder_description
        self._run_git(local_path, ['add', '-A'], f'failed to stage changes for branch {branch_name}')
        self._unstage_and_discard_generated_artifacts(local_path, branch_name, status_output)
        # Legacy path: an agent (or an older prompt) that still writes
        # ``validation_report.md`` into the repo root. Kept so such a file is
        # still stripped before the push instead of riding into the PR — its
        # text is only used when the task folder has none.
        validation_report_descriptions = self._unstage_and_read_validation_reports(
            local_path, branch_name, status_output
        )
        if task_folder_description:
            validation_report_descriptions = [task_folder_description]
        self._run_git(local_path, ['add', '-A'], f'failed to restage cleanup changes for branch {branch_name}')
        self._run_git(local_path, ['commit', '-m', commit_message], f'failed to commit changes for branch {branch_name}')
        self._ensure_clean_worktree(local_path, branch_name)
        return '\n\n'.join(validation_report_descriptions).strip()

    def _pr_description_from_task_folder(self, local_path: str) -> str:
        """Text of ``<task folder>/pr_description.md``, or ``''``.

        The task folder is the clone's PARENT — the one folder every repo of
        a task lives under, and the agent's ``--add-dir`` scope. Writing the
        description there instead of inside a clone is what makes "never
        committed" structural: git cannot stage a file outside its worktree.
        Shared by every repo of a multi-repo task, so each PR gets the same
        description.
        """
        task_folder = os.path.dirname(os.path.normpath(str(local_path or '')))
        if not task_folder:
            return ''
        return self._validation_report_text(
            os.path.join(task_folder, PR_DESCRIPTION_FILENAME),
        ) or ''

    def _unstage_publication_excluded_paths(
        self,
        local_path: str,
        status_output: str,
        repository=None,
    ) -> list[str]:
        """Unstage everything kato never publishes: reports + build artifacts.

        Same set ``_commit_branch_changes_if_needed`` excludes at publish
        time, but WITHOUT the ``clean`` — this runs mid-task, so the files
        must survive on disk (see the merge call site). Returns what it
        excluded so the caller can tell an emptied index from an untouched one.
        """
        excluded = [
            *self._validation_report_paths_from_status(status_output),
            *self._generated_artifact_paths_from_status(status_output),
        ]
        for path in excluded:
            self._run_git(
                local_path,
                ['reset', 'HEAD', '--', path],
                f'failed to exclude {path} from the in-progress commit',
                repository,
            )
        return excluded

    def _staged_paths(self, local_path: str) -> list[str]:
        output = self._git_stdout(
            local_path,
            ['diff', '--no-ext-diff', '--cached', '--name-only'],
            f'failed to inspect staged paths for repository at {local_path}',
        )
        return [line for line in output.splitlines() if line.strip()]

    def _unstage_and_discard_generated_artifacts(
        self,
        local_path: str,
        branch_name: str,
        status_output: str,
    ) -> None:
        for artifact_path in self._generated_artifact_paths_from_status(status_output):
            self._run_git(
                local_path,
                ['reset', 'HEAD', '--', artifact_path],
                f'failed to exclude generated artifact path {artifact_path} from branch {branch_name}',
            )
            self._run_git(
                local_path,
                ['clean', '-fd', '--', artifact_path],
                f'failed to clean generated artifact path {artifact_path} from branch {branch_name}',
            )

    def _unstage_and_read_validation_reports(
        self,
        local_path: str,
        branch_name: str,
        status_output: str,
    ) -> list[str]:
        descriptions: list[str] = []
        for validation_report_path in self._validation_report_paths_from_status(status_output):
            self._run_git(
                local_path,
                ['reset', 'HEAD', '--', validation_report_path],
                f'failed to exclude validation report file {validation_report_path} from branch {branch_name}',
            )
            # The report is published as a task comment, not as a committed file.
            full_path = os.path.join(local_path, validation_report_path)
            description = self._validation_report_text(full_path)
            if description is None:
                self.logger.warning(
                    'validation report file was reported by git status but missing at %s', full_path
                )
            elif not description:
                self.logger.warning('validation report file was empty at %s', full_path)
            else:
                descriptions.append(description)
            self._run_git(
                local_path,
                ['clean', '-fd', '--', validation_report_path],
                f'failed to clean validation report file {validation_report_path} from branch {branch_name}',
            )
        return descriptions

    def _ensure_branch_is_publishable(
        self,
        local_path: str,
        branch_name: str,
        destination_branch: str,
    ) -> None:
        # Refresh the destination ref FIRST. Everything below is a commit-count
        # comparison against it, and in a per-task workspace clone the local
        # ``master`` / ``origin/master`` refs are only as fresh as the last
        # fetch — which for a long-running task is whenever the workspace was
        # provisioned. A branch whose pull request was merged upstream hours
        # ago still looks "ahead" against that stale ref, so this method
        # returns "publishable", the provider is asked to open a pull request
        # that has nothing to merge, and the operator gets a bare
        # "400 Client Error" for a task that actually SHIPPED. The
        # already-merged branch below exists precisely to report that case
        # honestly, and it can only fire if the ref is current.
        self._refresh_destination_ref(local_path, destination_branch)
        comparison_ref = self._comparison_reference(local_path, destination_branch)
        ahead_count = self._ahead_count(local_path, comparison_ref, branch_name)
        if ahead_count >= 1:
            return
        # Zero commits ahead has two very different causes, and the
        # operator must be able to tell them apart from the one-line
        # skip message — otherwise an already-shipped task reads like
        # "create pull request failed".
        #
        # The discriminator is the BEHIND count (commits in the
        # comparison ref the branch lacks). ``_ahead_count`` with the
        # refs swapped is exactly that, so the black-box git lib needs
        # no new method:
        #   - behind >= 1: the comparison ref advanced PAST the
        #     branch while the branch holds nothing new — the branch's
        #     commits are already contained in it, i.e. this task's
        #     pull request was already merged upstream. A completed
        #     task, not a failure or an empty-handed agent run.
        #   - behind == 0: the branch is level with the comparison
        #     ref — the agent genuinely produced no commits here.
        #
        # (An ancestor check can't discriminate: ahead == 0 already
        # implies the branch tip is contained in the comparison ref,
        # so ``--is-ancestor`` is unconditionally true here.)
        behind_count = self._ahead_count(local_path, branch_name, comparison_ref)
        if behind_count >= 1:
            raise RepositoryHasNoChangesError(
                f'branch {branch_name} is already merged into {comparison_ref} '
                f'— nothing new to open a pull request for'
            )
        raise RepositoryHasNoChangesError(
            f'branch {branch_name} has no task changes ahead of {comparison_ref}'
        )

    def _ensure_branch_has_task_changes(
        self,
        local_path: str,
        branch_name: str,
        destination_branch: str,
    ) -> None:
        if self._working_tree_status(local_path):
            return
        self._ensure_branch_is_publishable(local_path, branch_name, destination_branch)

    def _publish_branch_updates(
        self,
        local_path: str,
        branch_name: str,
        destination_branch: str,
        commit_message: str,
        repository=None,
        *,
        restore_workspace: bool = True,
    ) -> str:
        validation_report_description = ''
        try:
            validation_report_description = self._prepare_branch_for_publication(
                local_path,
                branch_name,
                destination_branch,
                commit_message,
            )
            self._push_branch(local_path, branch_name, repository)
        finally:
            # Per-task workspace clones (``~/.kato/workspaces/<task_id>/<repo>/``)
            # are owned exclusively by one task — they must STAY on the
            # task branch across publish operations so the next push /
            # PR / Files-tab open finds the correct HEAD. Without this
            # guard, the on-demand "Push" UI button would push and then
            # restore to master; the subsequent "Pull request" click
            # would then fail with "expected branch X but found master".
            if restore_workspace and not _is_per_task_workspace_clone(repository):
                self._prepare_workspace_for_task(local_path, destination_branch, repository)
        return validation_report_description

    def _prepare_workspace_for_task(
        self,
        local_path: str,
        destination_branch: str,
        repository=None,
    ) -> None:
        current_branch = self._current_branch(local_path)
        if self._working_tree_status(local_path):
            # The THIRD route into the wipe, and the one with the most to
            # lose: this runs only for clones that are NOT per-task
            # workspaces, i.e. the operator's own checkout, after a publish.
            # ``update_source_to_task_branch`` refuses to stash for exactly
            # this folder because it is a RUNNING system — yet this path
            # went straight to reset --hard + clean -fd on it.
            #
            # Parked, not refused: restoring the source folder to its
            # destination branch is the documented behaviour here and other
            # callers depend on it. Only the silent discarding goes away.
            self._stash_before_forced_restore(
                repository if repository is not None
                else SimpleNamespace(id=Path(local_path).name, local_path=local_path),
            )
            current_branch = self._make_git_ready_for_work(
                local_path,
                destination_branch,
                repository,
            )
        current_branch = self._ensure_destination_branch_checked_out(
            local_path,
            destination_branch,
            current_branch,
        )
        self._validate_destination_branch_tracking_state(local_path, destination_branch)
        if self._uses_remote_destination_sync(repository):
            self._pull_destination_branch(local_path, destination_branch, repository)
        current_branch = self._current_branch(local_path)
        self._assert_current_branch(local_path, destination_branch, current_branch)
        self._ensure_clean_worktree(local_path, current_branch)

    def _prepare_workspace_for_branch(
        self,
        local_path: str,
        destination_branch: str,
        branch_name: str,
        repository=None,
    ) -> None:
        current_branch = self._current_branch(local_path)
        # Already prepared — do nothing at all.
        #
        # Preparing a branch is not a once-per-task event: the scan loop
        # re-runs pickup every tick, and the sync / push paths prepare again
        # before they publish. From the second pass onward the clone is
        # already on the task branch AND dirty, because that dirty tree is
        # the agent's work in progress. Falling through from here ran
        # ``_make_git_ready_for_work`` — checkout -f, reset --hard
        # origin/<destination>, clean -fd — and threw that work away; and
        # ``_ensure_clean_worktree`` below would then have refused the task
        # for having uncommitted changes anyway.
        #
        # That is the reported failure: "he will clone all the repos but
        # will not create the branch by the task name in them, all the repos
        # will sit on master and he will just delete the entire code from
        # some repos". "Some" because a repo the agent had not touched is
        # clean, so it never entered the wipe.
        #
        # Nothing below can improve on "the branch is already checked out",
        # and every other path here is destructive by design, so returning
        # is both the correct and the only safe answer.
        if current_branch and current_branch == normalized_text(branch_name):
            # Stale BUILD OUTPUT is still worth clearing on a reused branch,
            # and this helper is the one that can tell it apart from real
            # work: it acts only when the status contains nothing BUT
            # generated artifacts and validation reports, and returns
            # without touching anything the moment a source edit is mixed
            # in. So an untracked ``build/`` goes, and the agent's
            # half-finished change never does.
            status_output = self._working_tree_status(local_path)
            if status_output:
                self._discard_only_generated_artifacts(
                    local_path, status_output, current_branch,
                )
            return
        if self._working_tree_status(local_path):
            # Park the tree before the wipe.
            #
            # ``_make_git_ready_for_work`` is checkout -f → reset --hard →
            # clean -fd with no safety net, and it is right for the case it
            # was written for: a clone being made ready to START work. But a
            # clone that is dirty and NOT on its task branch is just as often
            # one that got stranded on the default branch — a prep that
            # failed, a run that died — and then went on to collect the
            # agent's output. Wiping it there is the "he will just delete the
            # entire code from some repos" report, reached by a second route
            # than the already-fixed one.
            #
            # We cannot tell the two apart from git state alone, so the tree
            # is stashed (untracked included, because clean -fd takes those
            # too) and the wipe proceeds. Nothing is lost either way, and a
            # genuinely disposable tree costs one unused stash entry.
            self._stash_before_forced_restore(repository)
            current_branch = self._make_git_ready_for_work(
                local_path,
                destination_branch,
                repository,
            )
        self._validate_destination_branch_tracking_state(local_path, destination_branch)
        if self._uses_remote_destination_sync(repository):
            self._fetch_origin_for_branch_preparation(local_path, repository)
        current_branch, should_sync_task_branch = self._ensure_task_branch_checked_out(
            local_path,
            destination_branch,
            branch_name,
            current_branch,
            repository=repository,
        )
        self._assert_current_branch(local_path, branch_name, current_branch)
        self._ensure_clean_worktree(local_path, current_branch)
        if should_sync_task_branch and self._uses_remote_destination_sync(repository):
            if self._sync_checked_out_task_branch(local_path, branch_name, repository):
                current_branch = self._current_branch(local_path)
                self._assert_current_branch(local_path, branch_name, current_branch)
                self._ensure_clean_worktree(local_path, current_branch)

    def _ensure_clean_worktree(self, local_path: str, current_branch: str = '') -> None:
        status_output = self._working_tree_status(local_path)
        if not status_output:
            return
        if self._discard_only_generated_artifacts(local_path, status_output, current_branch):
            status_output = self._working_tree_status(local_path)
            if not status_output:
                return
        status_details = status_output.strip()
        self.logger.warning(
            'repository at %s still has uncommitted changes on branch %s:\n%s',
            local_path,
            current_branch or '<unknown>',
            status_details,
        )
        raise RuntimeError(
            f'repository at {local_path} has uncommitted changes on branch '
            f'{current_branch or "<unknown>"}; refusing to start a new task\n'
            f'{status_details}'
        )

    def _discard_only_generated_artifacts(
        self,
        local_path: str,
        status_output: str,
        current_branch: str,
    ) -> bool:
        generated_artifact_paths = self._generated_artifact_paths_from_status(status_output)
        validation_report_paths = self._validation_report_paths_from_status(status_output)
        removable_paths = [*generated_artifact_paths, *validation_report_paths]
        if not removable_paths:
            return False
        if not self._status_contains_only_removable_artifacts(
            status_output,
            generated_artifact_paths,
            validation_report_paths,
        ):
            return False
        if not current_branch:
            return False
        self.logger.warning(
            'discarding generated artifacts on branch %s before continuing:\n%s',
            current_branch,
            status_output.strip(),
        )
        # Park them first. This was the ONE remaining path that destroyed
        # files with nothing to recover from — no stash, no commit, no
        # reflog entry.
        #
        # It looks safe because it only fires when the status contains
        # nothing but "generated artifacts", but that classification is a
        # bare top-level-name match against {build, dist, out, coverage,
        # target}. A repo whose deliverable or source genuinely lives under
        # one of those names — a static site in ``out/``, a Maven module in
        # ``target/`` — has the agent's entire output silently deleted on
        # the next tick. And ``clean -fd`` takes every untracked file, not
        # only the ones that were classified.
        #
        # Stashing keeps the cheap-cleanup behaviour (the tree still comes
        # out clean) while making a misclassification recoverable instead of
        # terminal. A genuinely disposable build directory costs one unused
        # stash entry.
        self._stash_before_forced_restore(
            SimpleNamespace(
                id=Path(local_path).name or 'repository', local_path=local_path,
            ),
        )
        self._run_git(
            local_path,
            ['checkout', '-f', current_branch],
            (
                f'failed to discard generated artifacts while resetting branch '
                f'{current_branch} at {local_path}'
            ),
        )
        self._run_git(
            local_path,
            ['clean', '-fd'],
            f'failed to remove generated artifacts while cleaning branch {current_branch}',
        )
        return True

    def _make_git_ready_for_work(
        self,
        local_path: str,
        destination_branch: str,
        repository=None,
    ) -> str:
        # GUARDS FIRST. Everything below this point is destructive:
        # ``checkout -f`` → ``reset --hard origin/<dest>`` → ``clean -fd``.
        #
        # (1) Never against the operator's own checkout. This is the method
        #     that actually destroys things, and it had no source-tree gate —
        #     only the branch-creating paths did. An operator watched their
        #     working file revert, an untracked file vanish and a local commit
        #     disappear from ``git log`` while kato was, in the same run,
        #     politely refusing to create a branch in that folder.
        #
        # (2) Never silently drop local COMMITS. The "you have N local
        #     commits, refusing to start a new task" check used to run AFTER
        #     this call, so on a dirty tree it could never fire — the reset
        #     had already discarded them. And the stash below parks the
        #     working tree only: a commit is not stashed, so that loss was
        #     unrecoverable outside the reflog. Checked here, before the
        #     reset, which is the only place it can do its job.
        source = self._source_tree_containing(local_path)
        if source is not None:
            raise RuntimeError(
                f'refusing to reset {local_path} — that is inside the source '
                f'tree ({source}), not a per-task workspace clone. kato never '
                f'discards work in the folders you work in.'
            )
        self._validate_destination_branch_tracking_state(
            local_path, destination_branch,
        )
        include_remote_sync = self._uses_remote_destination_sync(repository)
        self.logger.info(
            'making git ready before starting work at %s: %s',
            local_path,
            git_ready_command_summary(
                destination_branch,
                include_remote_sync=include_remote_sync,
            ),
        )
        if include_remote_sync:
            self._run_git(
                local_path,
                ['fetch', 'origin'],
                f'failed to fetch origin for repository at {local_path}',
                repository,
            )
        self._run_git(
            local_path,
            ['checkout', '-f', destination_branch],
            f'failed to switch repository at {local_path} to {destination_branch}',
            repository,
        )
        if include_remote_sync:
            self._run_git(
                local_path,
                ['reset', '--hard', f'origin/{destination_branch}'],
                (
                    f'failed to reset repository at {local_path} to '
                    f'origin/{destination_branch}'
                ),
                repository,
            )
        self._run_git(
            local_path,
            ['clean', '-fd'],
            f'failed to remove untracked files while cleaning repository at {local_path}',
            repository,
        )
        current_branch = self._current_branch(local_path)
        self._assert_current_branch(local_path, destination_branch, current_branch)
        self._ensure_clean_worktree(local_path, current_branch)
        return current_branch

    @staticmethod
    def _uses_remote_destination_sync(repository) -> bool:
        return bool(
            repository is not None
            and (
                normalized_text(text_from_attr(repository, 'remote_url'))
                or normalized_text(text_from_attr(repository, 'repo_slug'))
            )
        )

    def _ensure_destination_branch_checked_out(
        self,
        local_path: str,
        destination_branch: str,
        current_branch: str,
    ) -> str:
        if current_branch and current_branch != destination_branch:
            self._run_git(
                local_path,
                ['checkout', destination_branch],
                f'failed to switch repository at {local_path} to {destination_branch}',
            )
            current_branch = self._current_branch(local_path)
        self._assert_current_branch(local_path, destination_branch, current_branch)
        return current_branch

    def _ensure_task_branch_checked_out(
        self,
        local_path: str,
        destination_branch: str,
        branch_name: str,
        current_branch: str,
        repository=None,
    ) -> tuple[str, bool]:
        if current_branch == branch_name:
            return current_branch, True
        restored_branch, should_sync_task_branch = self._checkout_existing_task_branch(
            local_path,
            branch_name,
        )
        if restored_branch:
            return restored_branch, should_sync_task_branch
        current_branch = self._ensure_destination_branch_checked_out(
            local_path,
            destination_branch,
            current_branch,
        )
        # Fresh task-branch path: fast-forward the destination branch to
        # origin/<destination> before forking. Without this, a local
        # ``master`` that's behind the remote (typical immediately after
        # the previous task's PR was merged) would seed the new task
        # branch with stale code, and the agent's first commit would
        # silently re-introduce the just-merged changes on top.
        if self._uses_remote_destination_sync(repository):
            self._sync_destination_branch_to_origin(
                local_path, destination_branch, repository,
            )
        self._create_task_branch(local_path, branch_name, destination_branch)
        return self._current_branch(local_path), False

    def _sync_destination_branch_to_origin(
        self,
        local_path: str,
        destination_branch: str,
        repository,
    ) -> None:
        """Reset the local destination branch to ``origin/<destination>``.

        Idempotent and safe to call when the local branch is already at
        the remote head (the reset is a no-op). Loud failure if the
        remote ref is missing — the caller relies on a synced base.
        """
        self._run_git(
            local_path,
            ['reset', '--hard', f'origin/{destination_branch}'],
            (
                f'failed to fast-forward {destination_branch} to '
                f'origin/{destination_branch} at {local_path}'
            ),
            repository,
        )

    def _checkout_existing_task_branch(
        self,
        local_path: str,
        branch_name: str,
    ) -> tuple[str, bool]:
        local_branch_ref = f'refs/heads/{branch_name}'
        remote_branch_ref = f'refs/remotes/origin/{branch_name}'
        if self._git_reference_exists(local_path, local_branch_ref):
            self._run_git(
                local_path,
                ['checkout', branch_name],
                f'failed to switch repository at {local_path} to {branch_name}',
            )
            return self._current_branch(local_path), True
        if not self._git_reference_exists(local_path, remote_branch_ref):
            return '', False
        self._run_git(
            local_path,
            ['checkout', '-b', branch_name, f'origin/{branch_name}'],
            f'failed to restore branch {branch_name} from origin/{branch_name}',
        )
        return self._current_branch(local_path), False

    def _fetch_origin_for_branch_preparation(
        self,
        local_path: str,
        repository=None,
    ) -> None:
        self._run_git(
            local_path,
            ['fetch', 'origin'],
            f'failed to fetch origin before preparing branch at {local_path}',
            repository,
        )

    def _sync_checked_out_task_branch(
        self,
        local_path: str,
        branch_name: str,
        repository=None,
    ) -> bool:
        remote_branch = f'origin/{branch_name}'
        if not self._git_reference_exists(local_path, remote_branch):
            return False
        self.logger.info(
            'syncing branch %s with %s before starting work',
            branch_name,
            remote_branch,
        )
        self._rebase_branch_onto_remote(local_path, branch_name, remote_branch, repository)
        return True

    def _create_task_branch(
        self,
        local_path: str,
        branch_name: str,
        destination_branch: str,
    ) -> None:
        self._run_git(
            local_path,
            ['checkout', '-b', branch_name],
            f'failed to create branch {branch_name} from {destination_branch}',
        )

    @staticmethod
    def _assert_current_branch(
        local_path: str,
        destination_branch: str,
        current_branch: str,
    ) -> None:
        if current_branch == destination_branch:
            return
        raise RuntimeError(
            f'repository at {local_path} is on branch '
            f'{current_branch or "<unknown>"} instead of {destination_branch}'
        )

    def _validate_destination_branch_tracking_state(
        self,
        local_path: str,
        destination_branch: str,
    ) -> None:
        remote_reference = f'origin/{destination_branch}'
        if not self._git_reference_exists(local_path, remote_reference):
            return
        ahead_count, _ = self._left_right_commit_counts(
            local_path,
            destination_branch,
            remote_reference,
        )
        if ahead_count > 0:
            raise RuntimeError(
                f'destination branch {destination_branch} at {local_path} has '
                f'{ahead_count} local commit(s) not on {remote_reference}; '
                'refusing to start a new task'
            )

    def _refresh_destination_ref(self, local_path: str, destination_branch: str) -> None:
        """Best-effort ``git fetch origin <dest>`` before an ahead/behind check.

        BEST-EFFORT by design: a publish must not fail because the operator is
        offline or the remote briefly refused. On failure the comparison simply
        falls back to whatever ref is already on disk — the pre-existing
        behaviour — so this can only ever make the answer fresher.
        """
        normalized_branch = (destination_branch or '').strip()
        if not normalized_branch:
            return
        try:
            self._run_git(
                local_path,
                ['fetch', 'origin', normalized_branch],
                f'failed to refresh origin/{normalized_branch} before '
                f'checking whether the branch is publishable',
            )
        except Exception:
            self.logger.warning(
                'could not refresh origin/%s at %s — the merged/ahead check '
                'will use the ref already on disk, which may be stale',
                normalized_branch, local_path,
            )

    def _comparison_reference(self, local_path: str, destination_branch: str) -> str:
        # ``origin/<dest>`` FIRST. A per-task workspace clone never checks out
        # or pulls its local ``master``, so that ref is frozen at clone time
        # while ``origin/master`` is what ``_refresh_destination_ref`` just
        # updated. Preferring the local branch meant the freshly fetched ref
        # was ignored and the comparison stayed stale even after a good fetch.
        for reference in (f'origin/{destination_branch}', destination_branch):
            if self._git_reference_exists(local_path, reference):
                return reference
        raise RuntimeError(
            f'destination branch {destination_branch} is not available locally'
        )

    def current_head_sha(self, repository) -> str:
        """Return ``HEAD`` SHA of ``repository``'s checkout (empty on failure).

        Public so the review-fix path can snapshot HEAD before
        spawning the agent and compare after to verify the agent
        actually committed something. Without this check, an agent
        that ran but produced no edits would still get its reply
        posted and the comment resolved if the task branch had any
        prior commits ahead of base — leading to the "kato pushed a
        follow-up update" lie even when nothing was pushed.
        """
        local_path = str(getattr(repository, 'local_path', '') or '').strip()
        if not local_path:
            return ''
        try:
            return self._git_stdout(
                local_path,
                ['rev-parse', 'HEAD'],
                f'failed to read HEAD sha for {local_path}',
            ).strip()
        except Exception:
            return ''

    def has_dirty_working_tree(self, repository) -> bool:
        """True when the repository has uncommitted edits (tracked or untracked).

        Used alongside ``current_head_sha`` for the "did the agent do
        anything?" check. A clean tree + an unmoved HEAD is the
        unambiguous "nothing happened" signal.
        """
        local_path = str(getattr(repository, 'local_path', '') or '').strip()
        if not local_path:
            return False
        try:
            return bool(self._working_tree_status(local_path).strip())
        except Exception:
            return False

    @staticmethod
    def _validation_report_paths_from_status(status_output: str) -> list[str]:
        return validation_report_paths_from_status(status_output)

    @staticmethod
    def _validation_report_text(validation_report_full_path: str) -> str | None:
        if not os.path.exists(validation_report_full_path):
            return None
        return Path(validation_report_full_path).read_text(encoding='utf-8').strip()

    @staticmethod
    def _generated_artifact_paths_from_status(status_output: str) -> list[str]:
        return generated_artifact_paths_from_status(status_output)

    @classmethod
    def _status_contains_only_removable_artifacts(
        cls,
        status_output: str,
        generated_artifact_paths: list[str],
        validation_report_paths: list[str],
    ) -> bool:
        return status_contains_only_removable_artifacts(
            status_output,
            generated_artifact_paths,
            validation_report_paths,
        )
