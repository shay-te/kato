"""A task's repository set: what it touches, and keeping that in sync.

The third subsystem lifted out of ``AgentService``. One job: reconcile the
three places a task's repository list is written down — the ticket's
``kato:repo:`` tags, the workspace's ``.kato-meta.json``, and the clones on
disk — and let the operator add to it.

The tag→metadata reconcile is throttled per task (``_REPO_RECONCILE_TTL_SECONDS``)
because both the push and the pull-request path ask for it back to back, and
each miss costs a ticket lookup.

Collaborators may be passed as objects or wrapped in ``later(host, 'attr')``
when the host replaces them at runtime — see
``kato_core_lib.helpers.late_binding``.
"""

from __future__ import annotations

import os
import sys
import time

from kato_core_lib.helpers.deadline import run_with_deadline
from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.service_results import failure
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.task_lookup_utils import find_assigned_or_review_task

# A repo added to the ticket mid-task shows up as a tag long before anything
# re-reads the metadata, so push and PR both reconcile first. 20s is short
# enough that the operator sees their new repo on the next click and long
# enough that one Done press (push → PR) pays for a single ticket lookup.
_REPO_RECONCILE_TTL_SECONDS = 20.0

# How long to wait on the agent when computing the "restart the tab" hint.
# Short on purpose: the hint is cosmetic, the git work is already done, and an
# unresponsive agent must not keep the operator's UI waiting.
_SESSION_PROBE_TIMEOUT_SECONDS = 2.0


class TaskRepositoryService(object):
    """The repositories one task touches: list, add, sync, reconcile, search."""

    def __init__(
        self,
        *,
        repository_service,
        task_service,
        workspace_manager,
        session_manager=None,
        logger=None,
    ) -> None:
        self._get_repository_service = provider_for(repository_service)
        self._get_task_service = provider_for(task_service)
        self._get_workspace_manager = provider_for(workspace_manager)
        self._get_session_manager = provider_for(session_manager)
        self._logger_getter = provider_for(
            logger if logger is not None else configure_logger('TaskRepositoryService'),
        )
        # task_id -> monotonic timestamp of its last tag→metadata reconcile.
        self._repository_reconcile_at: dict[str, float] = {}

    @property
    def logger(self):
        """The host's CURRENT logger — resolved per call, never captured."""
        return self._logger_getter()

    @property
    def _repository_service(self):
        return self._get_repository_service()

    @property
    def _task_service(self):
        return self._get_task_service()

    @property
    def _workspace_manager(self):
        return self._get_workspace_manager()

    @property
    def _session_manager(self):
        return self._get_session_manager()

    def list_inventory_repositories(self) -> list[dict[str, str]]:
        """Return ``{id, owner, repo_slug, local_path}`` for every configured repo.

        Drives the Files-tab "Add repository" picker — the operator
        sees the full list of repos kato knows about (the repository
        inventory in the kato config) and picks one to attach to the
        current task. Repositories already on the task are filtered
        UI-side rather than here so the same payload can power other
        chooser UIs in the future.
        """
        try:
            inventory = self._repository_service.repositories
        except Exception:
            self.logger.exception('failed to list inventory repositories')
            return []
        out: list[dict[str, str]] = []
        for repo in inventory:
            out.append({
                'id': str(getattr(repo, 'id', '') or ''),
                'owner': str(getattr(repo, 'owner', '') or ''),
                'repo_slug': str(getattr(repo, 'repo_slug', '') or ''),
                'local_path': str(getattr(repo, 'local_path', '') or ''),
            })
        return out

    def add_task_repository(
        self, task_id: str, repository_id: str,
    ) -> dict[str, object]:
        """Tag the task with ``kato:repo:<id>`` and clone the repo.

        Drives the Files-tab "+ Add repository" flow. Two steps,
        run in order so a tag failure aborts cloning (the tag is what
        makes the resolution durable across kato restarts):

          1. ``task_service.add_tag(task_id, 'kato:repo:<id>')`` —
             idempotent on the platform side; YouTrack / Jira return
             cleanly if the tag already exists.
          2. ``sync_task_repositories(task_id)`` — provisions the
             new repo's clone into the per-task workspace via the
             same code path the operator's Sync icon uses, so a
             single missing repo is treated identically to a fresh
             multi-repo task.

        Returns the sync result enriched with ``tag_added`` so the
        UI toast can distinguish "already tagged, just cloned" from
        "tagged AND cloned".
        """
        normalized_task_id = str(task_id or '').strip()
        normalized_repo_id = str(repository_id or '').strip()
        if not normalized_task_id:
            return failure(
                'empty task id',
                flag='added',
            )
        if not normalized_repo_id:
            return failure(
                'empty repository id',
                flag='added',
            )
        # Defensive: only allow ids that exist in the inventory.
        # Without this, a typo or a stale tab could create a kato:repo:
        # tag pointing at a repo kato doesn't know about — the next
        # ``resolve_task_repositories`` would then raise on every
        # scan.
        try:
            inventory_ids = {
                str(getattr(r, 'id', '') or '').lower()
                for r in self._repository_service.repositories
            }
        except Exception:
            inventory_ids = set()
        if normalized_repo_id.lower() not in inventory_ids:
            self.logger.error(
                'add repository %s to task %s failed: not in the kato '
                'inventory (known: %s)',
                normalized_repo_id, normalized_task_id,
                ', '.join(sorted(inventory_ids)) or '<none>',
            )
            return failure(
                f'repository {normalized_repo_id!r} is not in the kato '
                    f'inventory; add it to the kato config under '
                    f'``repositories`` first',
                flag='added',
                task_id=normalized_task_id,
                repository_id=normalized_repo_id,
            )
        self.logger.info(
            'adding repository %s to task %s', normalized_repo_id, normalized_task_id,
        )
        from kato_core_lib.helpers.kato_tag_utils import build_repository_tag
        tag_name = build_repository_tag(normalized_repo_id)
        tag_added = False
        try:
            # Check whether the tag is already present so the toast can
            # report "tag already there" rather than implying we did
            # something we didn't.
            existing_task = self._lookup_task_for_sync(normalized_task_id)
            existing_tags = []
            if existing_task is not None:
                raw_tags = getattr(existing_task, 'tags', None) or []
                for entry in raw_tags:
                    if isinstance(entry, dict):
                        existing_tags.append(str(entry.get('name', '') or ''))
                    else:
                        existing_tags.append(
                            str(getattr(entry, 'name', entry) or ''),
                        )
            already_tagged = any(
                t.strip().lower() == tag_name.lower()
                for t in existing_tags
            )
            if not already_tagged:
                self._task_service.add_tag(normalized_task_id, tag_name)
                tag_added = True
        except Exception as exc:
            self.logger.exception(
                'failed to add tag %s to task %s', tag_name, normalized_task_id,
            )
            return failure(
                f'failed to tag task: {exc}',
                flag='added',
                task_id=normalized_task_id,
                repository_id=normalized_repo_id,
            )
        sync_result = self.sync_task_repositories(normalized_task_id)
        if not sync_result.get('synced'):
            self.logger.error(
                'add repository %s to task %s did not complete: %s',
                normalized_repo_id, normalized_task_id,
                sync_result.get('error')
                or self._describe_repository_failures(sync_result)
                or 'no repositories were added',
            )
        else:
            self.logger.info(
                'added repository %s to task %s', normalized_repo_id,
                normalized_task_id,
            )
        # Compose the response so the UI can show one toast for the
        # whole flow (tag + clone), not two.
        return {
            'added': bool(sync_result.get('synced')) or tag_added,
            'task_id': normalized_task_id,
            'repository_id': normalized_repo_id,
            'tag_added': tag_added,
            'tag_name': tag_name,
            'sync': sync_result,
        }

    def sync_task_repositories(self, task_id: str, task=None) -> dict[str, object]:
        """Add any task repos missing from the workspace; never remove.

        ``task`` is an already-fetched Task to resolve against — pass it
        from a caller that has one (the scan loop) so the reconcile costs
        no extra ticket-platform call. Omit it and the task is looked up.

        Drives the planning UI's "Sync repositories" icon on the Files
        tab: resolve the repo set the task touches (tags + description),
        compare it against the workspace, clone what is missing, and put
        those new clones on the task branch.

        Never removes repos from the workspace — repos that were cloned
        but are no longer on the task stay on disk so the operator can
        still inspect / commit them. Returns a per-repo summary the UI
        renders in the toast.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return failure('empty task id', flag='synced', task_id=task_id)
        self.logger.info('scanning task %s for repositories', normalized)
        if self._workspace_manager is None:
            return self._sync_failed(
                normalized, 'workspace manager not wired',
            )
        workspace = self._workspace_manager.get(normalized)
        if workspace is None:
            return self._sync_failed(
                normalized, 'no workspace exists for this task yet',
            )
        lookup_failures: list[str] = []
        task_obj = task if task is not None else self._lookup_task_for_sync(
            normalized, lookup_failures,
        )
        if task_obj is None:
            if lookup_failures:
                # The tracker errored rather than answering "no such task".
                # Saying "check that you are still the assignee" here is a
                # misdiagnosis: nothing about the ticket is known.
                return self._sync_failed(
                    normalized,
                    f'could not reach the ticket platform, so kato cannot tell '
                    f'whether {normalized} still exists — this is a connection '
                    f'or credentials problem, not a problem with the ticket. '
                    f'Failed queries: {"; ".join(lookup_failures)}',
                )
            return self._sync_failed(
                normalized,
                f'could not find {normalized} on the ticket platform — '
                f'check that you are still the assignee and that the '
                f'ticket is reachable from kato\'s configured queues',
            )
        try:
            task_repos = self._repository_service.resolve_task_repositories(task_obj)
        except Exception as exc:
            # The operator's most common cause lands here — a repo the task
            # references sits under AGENT_IGNORED_REPOSITORY_FOLDERS. Log the
            # reason so it is findable in the activity log, not only in the
            # toast the operator has to hover to read.
            self.logger.exception(
                'repository scan for task %s failed: %s', normalized, exc,
            )
            return failure(
                f'failed to resolve task repositories: {exc}',
                flag='synced',
                task_id=normalized,
            )
        existing_ids = {str(rid).lower() for rid in (workspace.repository_ids or [])}
        missing_repos = [
            r for r in task_repos
            if str(getattr(r, 'id', '') or '').lower() not in existing_ids
        ]
        already_present = [
            str(getattr(r, 'id', '') or '')
            for r in task_repos
            if str(getattr(r, 'id', '') or '').lower() in existing_ids
        ]
        self.logger.info(
            'repository scan for task %s found %d repositor%s (%d already '
            'in the workspace, %d to add%s)',
            normalized, len(task_repos), 'y' if len(task_repos) == 1 else 'ies',
            len(already_present), len(missing_repos),
            ': ' + ', '.join(
                str(getattr(r, 'id', '') or '') for r in missing_repos
            ) if missing_repos else '',
        )
        if not missing_repos:
            # Nothing to CLONE is not the same as nothing to do. A repo
            # already in the workspace can still be sitting on the remote's
            # default branch — its prep failed once, or a run died before it
            # ran — and returning here left it there permanently: sync said
            # "already present", push and PR skipped it because the task
            # branch did not exist, and clicking Sync again changed nothing.
            stranded = self._recover_stranded_clones(
                normalized, task_obj, task_repos, [],
            )
            return {
                'synced': not stranded,
                'task_id': normalized,
                'added_repositories': [],
                'already_present': already_present,
                'failed_repositories': stranded,
                'requires_session_restart': False,
            }
        added, failed_repositories, provisioned = self._clone_missing_repositories(
            normalized, task_obj, task_repos, missing_repos,
        )
        branch_prep_failures = self._put_new_clones_on_the_task_branch(
            normalized, task_obj, provisioned, missing_repos,
            already_failed=failed_repositories,
        )
        branch_prep_failures += self._recover_stranded_clones(
            normalized, task_obj, provisioned, missing_repos,
        )
        all_failures = failed_repositories + branch_prep_failures
        if all_failures:
            for entry in all_failures:
                self.logger.error(
                    'adding repository %s to task %s failed: %s',
                    entry.get('repository_id') or '<unknown>', normalized,
                    entry.get('error') or 'unknown error',
                )
        if added and not all_failures:
            self.logger.info(
                'added %s to task %s', ', '.join(added), normalized,
            )
        return {
            'synced': bool(added) and not failed_repositories and not branch_prep_failures,
            'task_id': normalized,
            'added_repositories': added,
            'already_present': already_present,
            'failed_repositories': all_failures,
            'requires_session_restart': self._sync_requires_session_restart(
                normalized, provisioned, missing_repos,
            ),
        }

    def _sync_failed(self, task_id: str, error: str) -> dict[str, object]:
        """Log the reason, then return the standard failure envelope.

        Every early return in ``sync_task_repositories`` used to be silent —
        the reason reached the UI toast only, so an operator who missed the
        toast had nothing to search for in the activity log.
        """
        self.logger.error(
            'repository scan for task %s failed: %s', task_id, error,
        )
        return failure(error, flag='synced', task_id=task_id)

    @staticmethod
    def _describe_repository_failures(sync_result: dict) -> str:
        """Flatten ``failed_repositories`` into one log-friendly line."""
        entries = sync_result.get('failed_repositories') or []
        return '; '.join(
            f"{entry.get('repository_id') or '<unknown>'}: "
            f"{entry.get('error') or 'unknown error'}"
            for entry in entries
        )

    def _clone_missing_repositories(
        self, task_id: str, task_obj, task_repos: list, missing_repos: list,
    ) -> tuple[list[str], list[dict[str, str]], list]:
        """Clone the repos the workspace is missing. Returns (added, failed, all).

        Passes the FULL task set to ``provision_task_workspace_clones``, not
        just the missing ones: that both updates the workspace metadata and
        skips the already-cloned repos through ``WorkspaceManager.create``'s
        dedupe, so the call stays idempotent.
        """
        from kato_core_lib.data_layers.service.workspace_provisioning_service import (
            provision_task_workspace_clones,
        )
        try:
            provisioned = provision_task_workspace_clones(
                self._workspace_manager,
                self._repository_service,
                task_obj,
                task_repos,
            ) or []
        except Exception as exc:
            self.logger.exception(
                'failed to sync repositories for task %s', task_id,
            )
            return [], [
                {'repository_id': str(getattr(r, 'id', '') or ''), 'error': str(exc)}
                for r in missing_repos
            ], []
        added = [str(getattr(r, 'id', '') or '') for r in missing_repos]
        return added, [], provisioned


    def _as_workspace_clone(self, task_id: str, repository):
        """``repository`` with ``local_path`` pointing at its workspace clone.

        The callers of this reach here holding INVENTORY objects on at least
        one path — the early "nothing to clone" return has no provisioned
        list to work from — and an inventory object's ``local_path`` is the
        OPERATOR'S OWN CHECKOUT. Recovering "onto the task branch" against
        that creates the task branch inside the folders they work in, which
        is the very failure this module keeps having to fix.

        Returns ``None`` when the clone path cannot be resolved, so the
        caller skips the repo rather than falling back to the source tree.
        """
        manager = self._workspace_manager
        if manager is None:
            return None
        try:
            clone_path = manager.repository_path(
                task_id, str(getattr(repository, 'id', '') or ''),
            )
        except Exception:
            return None
        if not clone_path:
            return None
        import copy as _copy
        rewritten = _copy.copy(repository)
        rewritten.local_path = str(clone_path)
        return rewritten

    def _recover_stranded_clones(
        self, task_id: str, task_obj, provisioned: list, missing_repos: list,
    ) -> list[dict[str, str]]:
        """Move ALREADY-PRESENT clones onto the task branch when they are not.

        Sync only branch-preps the repos it just added. A repo already listed
        in the workspace is reported as ``already_present`` and skipped — so
        one that got registered but never made it onto the task branch (its
        prep failed, or it was cloned by a run that died) stays on the
        remote's default branch permanently. Clicking Sync again changes
        nothing, and push / PR keep skipping it because the task branch does
        not exist. That is "it adds the repos and does the same thing, keeps
        it on master".

        Uses ``recover_clone_onto_task_branch``, NOT ``prepare_task_branches``:
        a stranded clone may well hold the agent's uncommitted work, and the
        prep path would wipe it to the destination branch. The recovery is a
        plain checkout that carries the working tree across, and refuses
        outright when the clone has its own commits on the wrong branch.
        """
        added_set = {str(getattr(r, 'id', '') or '').lower() for r in missing_repos}
        stranded = [
            self._as_workspace_clone(task_id, r)
            for r in provisioned
            if str(getattr(r, 'id', '') or '').lower() not in added_set
        ]
        stranded = [r for r in stranded if r is not None]
        failures: list[dict[str, str]] = []
        for repository in stranded:
            try:
                branch_name = self._repository_service.build_branch_name(
                    task_obj, repository,
                )
                reason = self._repository_service.recover_clone_onto_task_branch(
                    repository, branch_name,
                )
            except Exception as exc:
                reason = str(exc)
            if not reason:
                continue
            self.logger.error(
                'repository %s on task %s is not on its task branch and could '
                'not be moved onto it: %s',
                getattr(repository, 'id', '<unknown>'), task_id, reason,
            )
            failures.append({
                'repository_id': str(getattr(repository, 'id', '') or ''),
                'error': f'not on the task branch: {reason}',
            })
        return failures

    def _put_new_clones_on_the_task_branch(
        self, task_id: str, task_obj, provisioned: list, missing_repos: list,
        already_failed: list | None = None,
    ) -> list[dict[str, str]]:
        """Check the freshly-cloned repos out on the task branch.

        Critical, and the source of a silent failure when it is skipped: a
        fresh clone lands on the remote's default branch, so the agent commits
        to master locally and BOTH ``push_task`` and
        ``create_pull_request_for_task`` skip the repo —
        ``branch_needs_push(repo, <task branch>)`` is False for a branch that
        does not exist. The operator's symptom is changes that appear in the
        clone but never become a PR.
        """
        added_set = {str(getattr(r, 'id', '') or '').lower() for r in missing_repos}
        newly_provisioned = [
            r for r in provisioned
            if str(getattr(r, 'id', '') or '').lower() in added_set
        ]
        if not newly_provisioned:
            if not added_set:
                return []
            # Repos WERE meant to be added, but none of them came back from
            # provisioning — so nothing is branch-prepped and every one of
            # them stays on the remote's default branch. That is the exact
            # state the operator reported ("keeps it on master"), and
            # returning [] here made it look like a success: the sync toast
            # said the repos were added, push/PR then skipped them all
            # because the task branch never existed.
            # A repo whose CLONE already failed is reported by the clone
            # step. Reporting it again here as "stayed on the default
            # branch" is a second error for one cause, which reads as two
            # separate problems in the toast and the log.
            reported = {
                str(entry.get('repository_id') or '').lower()
                for entry in (already_failed or [])
            }
            unreported = [
                r for r in missing_repos
                if str(getattr(r, 'id', '') or '').lower() not in reported
            ]
            if not unreported:
                return []
            self.logger.error(
                'no provisioned clones came back for newly-added repositories '
                'on task %s (%s); they would stay on the default branch',
                task_id,
                ', '.join(sorted(
                    str(getattr(r, 'id', '') or '') for r in unreported
                )),
            )
            return [{
                'repository_id': str(getattr(r, 'id', '') or ''),
                'error': (
                    'the workspace clone was not available for branch '
                    'preparation, so this repo would stay on the default '
                    'branch and never produce a pull request'
                ),
            } for r in unreported]
        repository_branches = {
            repo.id: self._repository_service.build_branch_name(task_obj, repo)
            for repo in newly_provisioned
        }
        try:
            self._repository_service.prepare_task_branches(
                newly_provisioned, repository_branches,
            )
        except Exception as exc:
            self.logger.exception(
                'failed to prepare task branches for newly-synced '
                'repositories on task %s', task_id,
            )
            return [{
                'repository_id': str(getattr(r, 'id', '') or ''),
                'error': f'branch prep: {exc}',
            } for r in newly_provisioned]
        return []

    def reconcile_task_repositories(self, task_id: str, task=None) -> dict[str, object]:
        """Fold the task's ``kato:repo:`` tags into the workspace metadata.

        The tags on the ticket are the durable statement of which repos a
        task touches. ``.kato-meta.json``'s ``repository_ids`` is what
        every publish path actually reads (see
        ``_resolve_publish_context``). Those two drift apart the moment a
        repo is tagged mid-task — the agent says it needs one more repo,
        the operator adds it — and until something re-runs the resolution
        the publish paths keep working from the stale list: the new repo
        gets cloned and edited, then push / PR / Update source walk right
        past it. Nothing errors, so it reads as "kato pushed everything".

        So every operator publish action calls this first. It is
        :meth:`sync_task_repositories` (clone + task branch + metadata)
        with its failures downgraded to a log line — a ticket platform
        that is unreachable must not block pushing the repos kato already
        knows about. Throttled per task (``_REPO_RECONCILE_TTL_SECONDS``)
        so the Done button's push→PR pair costs one ticket lookup, not
        two.

        Returns the sync result (``{}`` when it could not run).
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return {}
        # No workspace yet = nothing to reconcile against. Checked here
        # (locally) so a task that has not been provisioned doesn't log a
        # failed sync on every scan tick.
        if self._workspace_manager is None:
            return {}
        try:
            if self._workspace_manager.get(normalized) is None:
                return {}
        except Exception:
            return {}
        now = time.monotonic()
        last = self._repository_reconcile_at.get(normalized, 0.0)
        if last and (now - last) < _REPO_RECONCILE_TTL_SECONDS:
            return {}
        self._repository_reconcile_at[normalized] = now
        try:
            result = self.sync_task_repositories(normalized, task=task) or {}
        except Exception:
            self.logger.exception(
                'repository reconcile failed for task %s; continuing with '
                'the repositories already in the workspace metadata',
                normalized,
            )
            return {}
        added = [str(r) for r in (result.get('added_repositories') or []) if r]
        if added:
            self.logger.info(
                'task %s: tags name repository(ies) %s that the workspace '
                'metadata was missing — cloned and recorded them',
                normalized, ', '.join(added),
            )
            if result.get('requires_session_restart'):
                # The CLI bakes its sandbox at spawn time, so a live chat
                # cannot write into a clone that appeared after it started.
                self.logger.info(
                    'task %s: restart the chat tab for the agent to reach %s',
                    normalized, ', '.join(added),
                )
        elif result.get('error'):
            # Not fatal: the caller works from the metadata as it stands.
            self.logger.warning(
                'repository reconcile skipped for task %s: %s',
                normalized, result.get('error'),
            )
        return result

    def discard_workspace_file_changes(
        self, task_id: str, repository_id: str, relative_paths: list[str],
        source: str = 'HEAD',
    ) -> list[str]:
        """Discard uncommitted changes to specific files in one task clone.

        Backs the Files-tree right-click "Discard changes". An OPERATOR action, not
        an agent one: it goes straight through kato's own git client, so it
        neither waits on a session nor spends a turn asking the agent to do
        it — and it works when no session is running at all.

        ``source`` is the ref the change is measured against — the caller
        passes the SAME base the Files tree colours against, so "discard"
        clears exactly what the tree calls a change. Anchored on HEAD it
        silently did nothing for a change already committed on the task
        branch.

        Returns the paths that actually had something to discard, so the UI
        can say "nothing to discard" rather than report a success that did
        nothing. Raises when the repo is unknown or git refuses.
        """
        task = str(task_id or '').strip()
        repository = str(repository_id or '').strip()
        if not task or not repository:
            raise ValueError('task id and repository id are required')
        local_path = self._workspace_manager.repository_path(task, repository)
        if not local_path or not os.path.isdir(local_path):
            raise ValueError(f'no clone for repository {repository} in task {task}')
        return self._repository_service.restore_paths(
            local_path, relative_paths, source=source,
        )

    def search_task_workspace(
        self, task_id: str, query: str, *, limit: int = 200,
    ) -> dict[str, object]:
        """Content (grep) search across every repo in the task's workspace.

        Runs ``git grep`` per repo clone (fast, respects .gitignore, covers
        the agent's untracked new files) and returns flat
        ``{repo_id, path, line, text}`` matches — what the Files-tab search
        shows so the operator can find a symbol like ``project_list`` by
        its CONTENT, not just by filename. Best-effort + capped.
        """
        normalized = str(task_id or '').strip()
        normalized_query = str(query or '').strip()
        if not normalized or not normalized_query or self._workspace_manager is None:
            return {'matches': [], 'truncated': False, 'query': normalized_query}
        try:
            workspace = self._workspace_manager.get(normalized)
        except Exception:
            workspace = None
        if workspace is None:
            return {'matches': [], 'truncated': False, 'query': normalized_query}
        repo_ids = list(getattr(workspace, 'repository_ids', None) or [])
        matches: list[dict] = []
        truncated = False
        for repo_id in repo_ids:
            if len(matches) >= limit:
                truncated = True
                break
            try:
                repo_path = str(
                    self._workspace_manager.repository_path(normalized, repo_id),
                )
            except Exception:
                continue
            if not repo_path:
                continue
            try:
                repo_matches = self._repository_service.git_grep(
                    repo_path, normalized_query, limit=limit - len(matches),
                )
            except Exception:
                self.logger.exception(
                    'content search failed for task %s repo %s',
                    normalized, repo_id,
                )
                continue
            for entry in repo_matches:
                matches.append({
                    'repo_id': repo_id,
                    # Absolute path so the editor (which loads by abs path)
                    # can open the hit directly.
                    'abs_path': os.path.join(repo_path, entry.get('path', '')),
                    **entry,
                })
        return {
            'matches': matches,
            'truncated': truncated or len(matches) >= limit,
            'query': normalized_query,
        }

    def _sync_requires_session_restart(
        self, task_id: str, provisioned: list, missing_repos: list,
    ) -> bool:
        """Did a live Claude session miss the newly-synced repo paths?

        The Claude CLI bakes its sandbox into the subprocess at spawn
        time — there is NO in-flight widening API. So when an operator
        clicks "Sync repositories" while a chat tab is already open,
        the disk gets the new clone but the live subprocess stays
        locked to its spawn-time ``--add-dir`` set and will refuse to
        write into the new repo. The UI needs an explicit signal to
        prompt the operator to restart the tab; that signal is this
        return value.

        Returns False when:
          * no live session for the task,
          * no session manager wired,
          * the session pre-dates ``allowed_additional_dirs`` (older
            subprocess; conservative — caller treats as "no signal"),
          * every newly-added repo's clone path is ALREADY in the
            session's allowed-dir set (e.g. the operator triggered
            a no-op resync after the spawn was widened by some other
            path).
        Returns True only when there is a live session AND at least
        one newly-cloned repo lives outside the session's sandbox.
        """
        if self._session_manager is None or not provisioned:
            return False
        # BEST-EFFORT, HARD-BOUNDED, and never on the critical path.
        #
        # This value is a UI HINT ("restart the tab to see the new repo"); the
        # git work has already finished by the time it is computed. An agent
        # that is down, wedged, or mid-teardown must never delay — let alone
        # block — an operator's git action. That is not hypothetical: an
        # unresponsive CLI once froze every git button in the UI through this
        # path, so the whole probe runs on a worker with a deadline and
        # answers "no hint" if it does not come back.
        return run_with_deadline(
            lambda: self._live_session_missing_paths(
                task_id, provisioned, missing_repos,
            ),
            seconds=_SESSION_PROBE_TIMEOUT_SECONDS,
            default=False,
            on_timeout=lambda: self.logger.warning(
                'agent session probe timed out for task %s; reporting no '
                'restart hint (the git work itself already succeeded)', task_id,
            ),
        )

    def _live_session_missing_paths(
        self, task_id: str, provisioned: list, missing_repos: list,
    ) -> bool:
        """The probe itself — see :meth:`_sync_requires_session_restart`."""
        get_session = getattr(self._session_manager, 'get_session', None)
        if not callable(get_session):
            return False
        session = get_session(task_id)
        if session is None or not getattr(session, 'is_alive', False):
            return False
        get_dirs = getattr(session, 'allowed_additional_dirs', None)
        if not callable(get_dirs):
            return False
        try:
            raw_dirs = get_dirs()
        except Exception:
            return False
        from pathlib import Path
        sandbox: set[str] = set()
        cwd = str(getattr(session, 'cwd', '') or '')
        if cwd:
            sandbox.add(str(Path(cwd)))
        for entry in raw_dirs or ():
            value = str(entry or '').strip()
            if value:
                sandbox.add(str(Path(value)))
        added_ids = {str(getattr(r, 'id', '') or '').lower() for r in missing_repos}
        for repo in provisioned:
            if str(getattr(repo, 'id', '') or '').lower() not in added_ids:
                continue
            local_path = str(getattr(repo, 'local_path', '') or '').strip()
            if not local_path:
                continue
            if str(Path(local_path)) not in sandbox:
                return True
        return False

    def _lookup_task_for_sync(self, task_id: str, failures: list | None = None):
        """Return the live Task for ``task_id`` (or ``None``).

        ``resolve_task_repositories`` needs the real Task — the
        workspace's ``task_summary`` stub doesn't carry tags or
        description, which are what drive multi-repo resolution. We
        scan the full lifecycle (assigned + review + done) so the
        sync icon works whenever a workspace exists, even after the
        ticket has moved past the autonomous queue states.

        ``failures`` collects the queues that ERRORED rather than simply
        not containing the task. The lookup returns ``None`` for both, and
        the two need opposite messages: one is "this ticket is not yours
        any more", the other is "kato cannot talk to the tracker at all".
        Reporting the second as the first is what sends an operator to
        re-check ticket assignees during a platform outage.
        """
        def record(queue_name: str) -> None:
            # Called from inside the helper's ``except`` block, so the
            # active exception is still available for the log line.
            self.logger.exception(
                'task lookup queue %s failed for %s', queue_name, task_id,
            )
            if failures is not None:
                reason = sys.exc_info()[1]
                failures.append(
                    f'{queue_name}: {reason}' if reason else queue_name,
                )

        return find_assigned_or_review_task(
            self._task_service, task_id, on_error=record,
        )
