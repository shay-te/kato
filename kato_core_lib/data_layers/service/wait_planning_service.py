"""Operator-hold short-circuit handling (``kato:wait-planning`` / ``kato:wait-editing``).

When a task carries either hold tag, the orchestrator skips
implementation/testing/publishing entirely and instead:

1. Resolves which repositories the task touches.
2. Provisions a per-task workspace folder + clones the repos into it.
3. Checks out the task branch on every cloned repo.
4. Spawns a long-lived Claude session so the user drives the conversation
   in the planning UI.
5. Moves the ticket to "In Progress".

Steps 1-3 and 5 are identical for both tags; only the opening prompt and the
permission mode differ, so the two holds share this one service rather than
forking a near-duplicate of the whole git dance:

* ``kato:wait-planning`` — **discussion only**. Runs in
  ``--permission-mode plan`` and the prompt forbids tool use outright.
* ``kato:wait-editing`` — **implementation, just not yet**. Keeps the
  configured permission mode and tells the agent to skip planning and edit
  directly once the operator gives the go-ahead. For work where you already
  know the fix and plan-then-work is pure latency.

This whole flow lived on :class:`AgentService` and crowded that god-class
with 11 wait-planning-specific methods. Pulling it out gives each class
a single reason to change: ``AgentService`` is the top-level scan-loop
orchestrator, ``WaitPlanningService`` owns the operator-hold workflow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from kato_core_lib.data_layers.data.fields import TaskTags
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.workspace_provisioning_service import (
    provision_task_workspace_clones,
)
from agent_core_lib.agent_core_lib.helpers import agent_prompt_utils
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.task_definition_prompt import task_definition_block
from kato_core_lib.helpers.task_execution_utils import skip_task_result
from kato_core_lib.helpers.workspace_refusal_guidance import KATO_AGENT_GUIDANCE
from kato_core_lib.helpers.workspace_repo_utils import (
    resolve_session_cwd,
    sibling_repository_dirs,
    task_workspace_root,
)
from utils_core_lib.utils_core_lib.text_utils import normalized_text, text_from_attr


@dataclass(frozen=True)
class _PlanningContext(object):
    """Where the chat session opens, and how far it may reach.

    ``workspace_root`` is the TASK FOLDER — the parent of every repo clone
    for this task. It is the boundary the docker sandbox bind-mounts and
    the one the prompt's STRICT BOUNDARY block names, so both agree.
    ``repository_paths`` are the individual clones, listed to the agent as
    the inventory of what is actually on disk.
    """

    cwd: str
    expected_branch: str
    workspace_root: str = ''
    repository_paths: tuple[str, ...] = ()
    additional_dirs: tuple[str, ...] = ()


# The two hold tags, in match order. ``permission_mode`` is the CLI override
# the spawn forces: wait-planning pins ``plan`` so the agent physically cannot
# execute even if the prompt fails to stop it, while wait-editing passes ''
# and inherits the runner's configured mode — its whole point is to be able to
# edit the moment the operator says go.
_WAIT_PLANNING_MODE = 'planning'
_WAIT_EDITING_MODE = 'editing'


class WaitPlanningService(object):
    """Owns the ``kato:wait-planning`` / ``kato:wait-editing`` hold lifecycle."""

    def __init__(
        self,
        *,
        session_manager,
        repository_service,
        task_state_service,
        workspace_manager=None,
        planning_session_runner=None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._session_manager = session_manager
        self._repository_service = repository_service
        self._task_state_service = task_state_service
        self._workspace_manager = workspace_manager
        self._planning_session_runner = planning_session_runner
        self.logger = logger or configure_logger(self.__class__.__name__)

    # ----- public API -----

    @staticmethod
    def _task_has_tag(task: Task, wanted: str) -> bool:
        tags = getattr(task, 'tags', None) or []
        target = wanted.lower()
        for tag in tags:
            if str(tag or '').strip().lower() == target:
                return True
        return False

    @classmethod
    def task_has_wait_planning_tag(cls, task: Task) -> bool:
        return cls._task_has_tag(task, TaskTags.WAIT_PLANNING)

    @classmethod
    def task_has_wait_editing_tag(cls, task: Task) -> bool:
        return cls._task_has_tag(task, TaskTags.WAIT_EDITING)

    @classmethod
    def _hold_mode(cls, task: Task) -> str:
        """Which hold applies to ``task`` (``''`` when neither does).

        Planning wins when both tags are present: it is the strictly more
        restrictive hold, and silently letting the agent edit because a
        second tag was left on the ticket is the wrong way to resolve a
        contradiction.
        """
        if cls.task_has_wait_planning_tag(task):
            return _WAIT_PLANNING_MODE
        if cls.task_has_wait_editing_tag(task):
            return _WAIT_EDITING_MODE
        return ''

    def handle_task(self, task: Task) -> dict[str, object] | None:
        """If ``task`` carries a hold tag, register the chat tab and stop.

        The orchestrator does no implementation/testing/publishing for
        these tasks — the human drives the conversation in the UI.
        Returns ``None`` to let the autonomous flow run; returns a skip
        result when a hold short-circuit took the wheel.
        """
        mode = self._hold_mode(task)
        if not mode:
            return None
        tag = (
            TaskTags.WAIT_PLANNING if mode == _WAIT_PLANNING_MODE
            else TaskTags.WAIT_EDITING
        )
        if self._session_manager is None:
            # No streaming backend (e.g. OpenHands) — nothing to register.
            self.logger.info(
                'task %s has %s but the active backend has no streaming UI; skipping',
                task.id,
                tag,
            )
            return skip_task_result(task.id, [])
        if self._is_chat_already_alive(task):
            return skip_task_result(task.id, [])
        context = self._resolve_planning_context(task)
        self._spawn_planning_session(task, context, mode)
        # Planning is real work — move the ticket out of the inbox so
        # it doesn't get picked up by another agent / scanned again as
        # "needs to start". Idempotent on the ticket side, and only
        # called on the fresh-spawn branch (the alive guard above
        # protects the steady state).
        self._move_to_in_progress(task)
        return skip_task_result(task.id, [])

    # ----- internals -----

    def _is_chat_already_alive(self, task: Task) -> bool:
        existing = self._session_manager.get_session(str(task.id))
        return existing is not None and existing.is_alive

    def _spawn_planning_session(
        self,
        task: Task,
        context: _PlanningContext,
        mode: str = _WAIT_PLANNING_MODE,
    ) -> None:
        # Belt-and-suspenders for wait-planning: the prompt explicitly forbids
        # tool use, AND the CLI runs in ``--permission-mode plan`` so Claude
        # can't execute even if it tries. Removing the tag flips back to the
        # configured permission mode via the autonomous path.
        #
        # wait-editing deliberately does NOT pin a mode: it inherits the
        # runner's configured one so the agent can start editing the instant
        # the operator says go. Forcing ``plan`` here would recreate exactly
        # the plan-then-work latency the tag exists to avoid.
        permission_mode = 'plan' if mode == _WAIT_PLANNING_MODE else ''
        prompt = (
            self._build_planning_prompt(task, context)
            if mode == _WAIT_PLANNING_MODE
            else self._build_editing_prompt(task, context)
        )
        tag = (
            TaskTags.WAIT_PLANNING if mode == _WAIT_PLANNING_MODE
            else TaskTags.WAIT_EDITING
        )
        try:
            self._start_hold_session(task, context, prompt, permission_mode)
            self._mark_workspace_waiting_for_operator(task)
            self.logger.info(
                'task %s tagged %s — registered %s chat (cwd=%s, task folder=%s); '
                'remove the tag to let the agent run autonomously',
                task.id,
                tag,
                mode,
                context.cwd or '?',
                context.workspace_root or '?',
            )
        except Exception:
            self.logger.exception(
                'failed to register %s session for task %s', mode, task.id,
            )

    def _start_hold_session(
        self,
        task: Task,
        context: _PlanningContext,
        prompt: str,
        permission_mode: str,
    ):
        """Spawn the hold session through the runner's single spawn funnel.

        This used to call ``session_manager.start_session`` directly with a
        hand-copied subset of the runner's defaults, and that subset was
        missing everything that tells the agent WHERE it is: no
        ``sandbox_root`` (so the docker sandbox mounted only ``cwd`` — the
        FIRST repo clone — hiding every sibling repo of the same task) and no
        ``additional_dirs`` (so a multi-repo task's agent had no ``--add-dir``
        for the other clones). It also silently dropped the architecture doc,
        the lessons file, docker mode, the per-task plan-mode lock, the
        per-task backend defaults (a Codex tab got the Claude binary) and the
        Remote Control bridge — every one of which the runner's funnel
        applies.

        The visible symptom was the operator having to paste the clone
        directory into the chat before the agent could do anything: kato had
        opened the session with no boundary block, no repo inventory and a
        sandbox that did not cover the task folder.

        ``claude -p --input-format stream-json`` stays alive across multiple
        user messages, but it must receive at least one prompt at startup —
        empty stdin makes it exit with an error and the scan loop would
        respawn it forever. The hold prompt puts the agent in "ready,
        waiting" state without kicking off any work.
        """
        runner = self._planning_session_runner
        if runner is None:
            # No runner means no spawn defaults to apply — the backend has no
            # interactive CLI session (OpenHands). ``handle_task`` already
            # refuses that case for a missing session manager; this is the
            # same refusal for the missing runner.
            raise RuntimeError(
                f'cannot start a hold session for task {task.id}: '
                'no planning session runner is wired'
            )
        return runner.start_session(
            task_id=str(task.id),
            task_summary=str(task.summary or ''),
            initial_prompt=prompt,
            cwd=context.cwd,
            branch_name=context.expected_branch,
            permission_mode=permission_mode,
            # The TASK FOLDER, not ``cwd``: the docker sandbox bind-mounts
            # this, and it is what the prompt's STRICT BOUNDARY block names,
            # so the container and the prompt agree on one boundary.
            workspace_root=context.workspace_root,
            additional_dirs=list(context.additional_dirs),
        )

    def _mark_workspace_waiting_for_operator(self, task: Task) -> None:
        if self._workspace_manager is None:
            return
        try:
            self._workspace_manager.update_resume_on_startup(str(task.id), False)
        except Exception:
            self.logger.exception(
                'failed to mark planning workspace %s as operator-driven',
                task.id,
            )

    def _move_to_in_progress(self, task: Task) -> None:
        """Best-effort ticket-state move. Failures log but never block the chat."""
        try:
            self._task_state_service.move_task_to_in_progress(task.id)
            self.logger.info(
                'task %s moved to in progress for planning session', task.id,
            )
        except Exception:
            self.logger.exception(
                'failed to move planning task %s to in progress', task.id,
            )

    def _resolve_planning_context(self, task: Task) -> _PlanningContext:
        """Resolve + clone + check-out branches; return where the chat opens.

        Best-effort: any failure (no repo match, git fetch error, etc.)
        falls back to a more conservative result so the chat tab still
        opens — the user sees an empty Files / Changes pane and can
        investigate, but the conversation isn't blocked.

        The workspace scope is resolved on EVERY return, including the
        degraded ones. A hold session whose branch prep failed still lives in
        a real task folder, and telling the agent where that folder is costs
        nothing — while omitting it is exactly how the agent ended up asking
        the operator to name its own clone directory.
        """
        repositories = self._resolve_repositories(task)
        if not repositories:
            return self._planning_context(task, [], '', '')
        repositories = self._provision_workspace(task, repositories)
        repositories = self._prepare_repositories(task, repositories)
        if not repositories:
            return self._planning_context(task, [], '', '')
        primary = repositories[0]
        cwd = text_from_attr(primary, 'local_path')
        branch_name = self._build_branch_name(task, primary)
        if not branch_name:
            return self._planning_context(task, repositories, cwd, '')
        if not self._check_out_branches(task, repositories, branch_name):
            return self._planning_context(task, repositories, cwd, '')
        return self._planning_context(task, repositories, cwd, branch_name)

    def _planning_context(
        self,
        task: Task,
        repositories: list,
        cwd: str,
        branch_name: str,
    ) -> _PlanningContext:
        """Bundle the cwd/branch with the task's on-disk scope."""
        task_id = str(task.id)
        repository_paths = tuple(
            path for path in (
                normalized_text(text_from_attr(repo, 'local_path'))
                for repo in repositories
            ) if path
        )
        return _PlanningContext(
            # Never spawn outside the task's own workspace. Repo resolution
            # can degrade to no repos (a clone failure, an unmatched tag), and
            # the empty cwd that produced used to be silently replaced with
            # kato's own working directory at spawn time — and then persisted.
            cwd=resolve_session_cwd(self._workspace_manager, task_id, cwd),
            expected_branch=branch_name,
            workspace_root=task_workspace_root(self._workspace_manager, task_id),
            repository_paths=repository_paths,
            additional_dirs=tuple(
                sibling_repository_dirs(self._workspace_manager, task_id),
            ),
        )

    def _resolve_repositories(self, task: Task) -> list:
        return self._safe_call(
            task,
            'resolve repositories for wait-planning task %s',
            fallback=[],
            action=lambda: list(
                self._repository_service.resolve_task_repositories(task) or [],
            ),
        )

    def _provision_workspace(self, task: Task, repositories: list) -> list:
        """Clone the task's repos into its workspace. NEVER falls back to the
        operator's own checkouts.

        This used to pass ``fallback=repositories`` — the INVENTORY objects,
        whose ``local_path`` is the operator's source tree. Any failure inside
        cloning (a transient git error, a file lock, a full disk) was caught
        by ``_safe_call`` and those source repos were handed straight on to
        ``_check_out_branches``, which created the task branch inside the
        folders the operator works in every day, and to ``cwd``, which pointed
        the agent at them. The task's own clone meanwhile sat on master and
        never produced a PR — "this kato release creates branches inside the
        dev repo... and keeps the task repos on master that claude works on".

        ``task_preflight_service._provision_workspace_clones`` was hardened
        against exactly this and says why: "hard-fail is the only safe default
        for a workspace-mode install". Wait-planning shares the helper but was
        left with the old fallback, so the autonomous flow was safe and the
        chat flow was not.

        Degrading to NO repos is the correct failure: the chat tab still
        opens (the caller turns an empty list into an empty cwd), the operator
        sees an empty Files pane and can investigate. An empty pane is a
        visible problem; a branch in the wrong repository is a silent one.

        With no workspace manager wired the helper is a documented no-op for
        legacy single-clone installs — there the inventory clone IS the only
        checkout, so it stays the correct answer.
        """
        workspace_mode = self._workspace_manager is not None
        return self._safe_call(
            task,
            'provision workspace clones for wait-planning task %s; '
            'refusing to fall back to the source checkouts',
            fallback=[] if workspace_mode else repositories,
            action=lambda: provision_task_workspace_clones(
                self._workspace_manager,
                self._repository_service,
                task,
                repositories,
            ),
        )

    def _prepare_repositories(self, task: Task, repositories: list) -> list:
        return self._safe_call(
            task,
            'prepare repositories for wait-planning task %s',
            fallback=[],
            action=lambda: list(
                self._repository_service.prepare_task_repositories(repositories) or [],
            ),
        )

    def _build_branch_name(self, task: Task, primary_repository) -> str:
        return self._safe_call(
            task,
            'derive branch name for wait-planning task %s',
            fallback='',
            action=lambda: str(
                self._repository_service.build_branch_name(task, primary_repository) or '',
            ).strip(),
        )

    def _safe_call(self, task: Task, action_label: str, *, fallback, action):
        """Run ``action()``; log + return ``fallback`` on any exception.

        Wait-planning is a best-effort flow: every git step has a
        sensible degradation (empty cwd, empty branch name, etc) so the
        chat tab still opens. Centralizing the boilerplate keeps each
        step tiny and readable.
        """
        try:
            return action()
        except Exception:
            self.logger.exception(action_label, task.id)
            return fallback

    def _check_out_branches(
        self,
        task: Task,
        repositories: list,
        branch_name: str,
    ) -> bool:
        repository_branches = {repo.id: branch_name for repo in repositories}
        try:
            self._repository_service.prepare_task_branches(
                repositories, repository_branches,
            )
        except Exception:
            self.logger.exception(
                'failed to check out task branch for wait-planning task %s; '
                'chat will open on whatever branch is currently checked out',
                task.id,
            )
            return False
        return True

    @staticmethod
    def _workspace_scope_sections(context: _PlanningContext | None) -> list[str]:
        """The STRICT BOUNDARY block + the on-disk repo inventory.

        Both hold prompts open with this, and it goes FIRST — the block's own
        first line is "read this first", and a boundary buried under the
        ticket text is not a boundary the agent reads first.

        Neither hold prompt had it. The autonomous run and the chat-send
        route both emit it, so the ONE surface the operator actually drives
        was the only one that never told the agent where its world is: it
        opened with a ticket description, no paths, and a rule saying the
        operator still owed it "the clone directory to work in". The agent
        did exactly what it was told — guessed at
        ``~/<agent-home>/workspaces/<TASK>``, found nothing, and asked the
        operator to paste the path.

        The task folder goes first in the scope list so
        ``workspace_scope_block``'s redundant-descendant collapse reduces
        every repo clone under it to that one boundary — and that boundary
        still covers a repo attached to the task after this prompt was built.
        """
        if context is None:
            return []
        scope_paths = [
            path for path in (
                context.workspace_root, *context.repository_paths, context.cwd,
            ) if path
        ]
        sections: list[str] = []
        scope = agent_prompt_utils.workspace_scope_block(
            scope_paths, extra_refusal_guidance=KATO_AGENT_GUIDANCE,
        )
        if scope:
            sections.extend([scope, ''])
        inventory = agent_prompt_utils.workspace_inventory_block(
            context.cwd,
            [path for path in context.repository_paths if path != context.cwd],
        )
        if inventory:
            sections.extend([inventory, ''])
        return sections

    @classmethod
    def _hold_prompt_preamble(
        cls,
        task: Task,
        opening: str,
        context: _PlanningContext | None = None,
    ) -> list[str]:
        """Workspace scope + opening line + ticket text + forbidden repos.

        Shared by both hold prompts — the only difference between them is the
        operating rules that follow, so everything up to that point lives here
        instead of being written twice and drifting.
        """
        task_id = text_from_attr(task, 'id')
        header = f'ticket {task_id}' if task_id else 'this task'
        sections = [
            *cls._workspace_scope_sections(context),
            opening.format(header=header),
            '',
        ]
        # Always framed as untrusted: the summary/description are tracker text
        # that anyone with comment access there can write.
        definition = task_definition_block(
            task_id=task_id,
            summary=text_from_attr(task, 'summary'),
            description=text_from_attr(task, 'description'),
        )
        sections.append(definition or '## Task definition\n(no description provided)')
        forbidden_repositories = agent_prompt_utils.forbidden_repository_guardrails_text()
        if forbidden_repositories:
            sections.extend(['', '## Forbidden repositories', forbidden_repositories])
        return sections

    @classmethod
    def _build_planning_prompt(
        cls, task: Task, context: _PlanningContext | None = None,
    ) -> str:
        """Initial prompt for a wait-planning chat tab.

        Four jobs at once:
          1. Name the task folder and the repos on disk (the STRICT
             BOUNDARY block), so the agent knows where it is and can't
             wander outside.
          2. Hand Claude the full task description so it has context.
          3. Hard-stop any tool use — wait-planning is **planning only**.
             We have to be explicit because the agent's default behavior
             when handed a task is to start working on it.
          4. Avoid empty stdin (which makes ``claude -p`` exit with an
             error and the scan loop would respawn it forever).
        """
        sections = cls._hold_prompt_preamble(
            task, "You're pair-planning with the user on {header}.", context,
        )
        sections.extend([
            '',
            '## Operating rules — READ CAREFULLY',
            '- This is a **planning-only** session. DO NOT call any tools.',
            '- DO NOT read, edit, write, or run anything.',
            '- DO NOT touch the filesystem, the shell, or the network.',
            '- Your job is to discuss the task with the user, ask clarifying '
            'questions, and help them refine the approach in plain text.',
            '- The user will explicitly tell you when planning is done. Until '
            'then, every reply is a discussion message — no tool calls.',
            *cls._done_sentinel_section(),
            '',
            'Briefly acknowledge that you understand and are ready to plan. '
            'Then wait for the user to drive the conversation.',
        ])
        return '\n'.join(sections)

    @classmethod
    def _build_editing_prompt(
        cls, task: Task, context: _PlanningContext | None = None,
    ) -> str:
        """Initial prompt for a wait-editing chat tab.

        Same hold as wait-planning — the agent must not start until the
        operator says go — but the opposite instruction once it does: no
        plan, no proposal round, edit directly. The operator reaches for this
        tag precisely when they already know the fix and plan-then-work is
        wasted latency, so a prompt that merely *allows* implementation is not
        enough; the agent's default on being handed a task is to plan first,
        and that default has to be named and overridden explicitly.

        Reading the workspace while parked is deliberately allowed — the hold
        is on EDITING, not on orientation, and an agent that has already read
        the relevant files starts faster when the go-ahead lands.
        """
        sections = cls._hold_prompt_preamble(
            task, "You're working with the user on {header}.", context,
        )
        sections.extend([
            '',
            '## Operating rules — READ CAREFULLY',
            # NOT "they still owe you the clone directory to work in". That
            # sentence used to be here, and it was a lie kato told the agent
            # about its own setup: the workspace is already cloned and its
            # path is in the boundary block above. The agent believed it,
            # waited to be told where to work, and the operator had to paste
            # the path by hand on every hold task.
            '- **Do not start yet.** Wait for the operator to explicitly tell '
            'you to go. Your workspace is already on disk at the task folder '
            'named above — you do not need to be told where to work, only '
            'what to do. They may still hand you more context for this task.',
            '- **Do not produce a plan.** No proposal, no numbered approach, '
            'no "here is what I would do" round. When the go-ahead arrives, '
            'start editing directly.',
            '- You MAY read files and look around the workspace while you '
            'wait, so you are oriented when the operator says go.',
            '- DO NOT edit, write, create, delete, or run anything until the '
            'operator gives the go-ahead.',
            *cls._done_sentinel_section(),
            '',
            'Reply with one short line confirming you are ready and waiting '
            'for the go-ahead. Do not summarize the task back, and do not '
            'suggest an approach.',
        ])
        return '\n'.join(sections)

    @staticmethod
    def _done_sentinel_section() -> list[str]:
        """The "emit this token when the operator says we're done" contract.

        Identical for both holds — kato watches for the sentinel on either,
        so the wording lives in one place.
        """
        from kato_core_lib.data_layers.data.sentinels import KATO_TASK_DONE_SENTINEL
        return [
            '',
            '## How to signal "I am done" — IMPORTANT',
            f'When the operator confirms the task is complete and they are '
            f'satisfied with the result, end your final reply with this exact '
            f'token on its own line: {KATO_TASK_DONE_SENTINEL}',
            'Kato watches for this token. When it appears, kato will push '
            'pending changes, open a pull request if one does not exist, and '
            'move the ticket to In Review automatically. Emit it ONLY when '
            'the operator has clearly said the work is done — never as part '
            'of a question, an apology, or speculative text.',
        ]
