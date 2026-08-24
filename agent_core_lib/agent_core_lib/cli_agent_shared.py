"""Behaviour shared by every CLI-driven agent transport.

The Claude and Codex transports were written independently and drifted into
~600 lines of parallel code, including **13 byte-identical methods**. This
mixin owns the identical ones so a fix lands in every transport at once.

DELIBERATELY NOT A FULL BASE CLASS. An earlier design put the whole transport
here — spawn, subprocess handling and the Docker sandbox wrap — with the
sandbox wrapper as a defaulted hook. That was rejected, correctly: this library
may not import the sandbox library, so a defaulted wrapper makes containment
FAIL OPEN — a subclass that forgets to override it silently runs the agent
unsandboxed, with nothing at the call site to reveal it.

So the split is drawn at the spawn boundary:

  * HERE — orchestration and pure logic that never touches a process.
  * THE TRANSPORT — everything that spawns, and therefore everything that
    sandboxes. ``_run_prompt_result`` stays in the transport where the Docker
    wrap is readable at the site that uses it.

The hooks below are ABSTRACT, never defaulted. A transport that forgets one
fails loudly on first use instead of quietly doing the wrong thing.

Required of the concrete transport:
  attributes ``_binary``, ``_binary_path``, ``_repository_root_path``,
  ``_model_smoke_test_enabled``, ``_model_access_smoke_test_ran``, ``logger``
  methods   ``_run_prompt_result``, ``_build_implementation_prompt``,
  ``_build_testing_prompt``, ``_tool_guardrails_text``,
  ``_run_model_access_validation``, ``fix_review_comments``
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from agent_core_lib.agent_core_lib.helpers import agent_prompt_utils
from agent_core_lib.agent_core_lib.helpers.agents_instruction_utils import (
    agents_instructions_for_path,
)
from agent_core_lib.agent_core_lib.helpers.comment_prompt import (
    CommentThreadSpec,
    build_comment_prompt_context,
)
from agent_core_lib.agent_core_lib.helpers.cli_shim_utils import (
    resolve_windows_cli_invocation,
)
from agent_core_lib.agent_core_lib.helpers.result_utils import build_openhands_result
from agent_core_lib.agent_core_lib.helpers.credential_scan import (
    scan_text_for_credentials_and_phishing,
)
from utils_core_lib.utils_core_lib.text_utils import (
    normalized_text,
    text_from_attr,
)


class CliAgentSharedBehaviour(object):
    """Mixin: the transport-agnostic half of a CLI agent client."""

    #: Human-readable CLI name used in operator-facing errors and log lines
    #: ('Claude', 'Codex'). Every transport MUST set it — the default is a
    #: sentinel rather than a plausible-looking string so a missing override
    #: shows up in the message instead of silently mislabelling another
    #: vendor's CLI in an error the operator is trying to act on.
    CLI_DISPLAY_NAME = '<unnamed CLI>'

    # ----- hooks the concrete transport MUST provide -------------------
    # Abstract on purpose. See the module docstring: a defaulted hook here
    # would let a transport silently lose its sandbox or its prompts.

    def _run_prompt(self, **kwargs) -> dict:
        """Spawn the CLI for one prompt and return its raw payload.

        The genuinely per-transport half: flags, sandbox wrapping, session
        resume, and how the CLI reports what it did (Claude's single JSON
        object vs Codex's JSONL event stream).
        """
        raise NotImplementedError('transport must implement _run_prompt')

    def _run_prompt_result(
        self,
        *,
        prompt: str,
        cwd: str,
        additional_dirs: list[str] | None = None,
        branch_name: str = '',
        default_commit_message: str | None = None,
        agent_session_id: str = '',
        log_label: str = '',
        task_id: str = '',
    ) -> dict[str, str | bool]:
        """Spawn the CLI for one prompt and shape the orchestrator's result.

        Both transports had this byte-for-byte: call ``_run_prompt``, hand the
        payload to the shared result builder. Only ``_run_prompt`` differs, so
        only ``_run_prompt`` is abstract.
        """
        payload = self._run_prompt(
            prompt=prompt,
            cwd=cwd,
            additional_dirs=list(additional_dirs or []),
            agent_session_id=agent_session_id,
            log_label=log_label,
            task_id=task_id,
        )
        return build_openhands_result(
            payload,
            branch_name=branch_name,
            default_commit_message=default_commit_message,
        )

    @classmethod
    def _tool_guardrails_text(cls) -> str:
        """The tool-use rules, in this CLI's own tool vocabulary.

        Abstract because the vocabulary is not cosmetic: Claude has named
        tools (``Edit``/``Write``/``Read``/``Bash``) while Codex exposes
        generic tooling, and naming a tool the agent does not have turns a
        guardrail into noise it learns to skip.

        A ``classmethod`` because the prompt builders that consume it are
        themselves classmethods — several are called on the CLASS in tests and
        in the streaming path, with no instance in hand.
        """
        raise NotImplementedError('transport must implement _tool_guardrails_text')

    def _system_prompt_addendum(self) -> str:
        """Content to prepend to the prompt body, or ``''`` to prepend nothing.

        A CLI with an ``--append-system-prompt`` flag delivers the architecture
        doc and lessons through it and returns ``''`` here. A CLI without one
        (Codex) has no system channel at all, so it returns that same payload
        and it rides in on the user prompt instead. Everything else about the
        prompt is identical either way, which is why this is a hook and not a
        second copy of the prompt.
        """
        return ''

    @classmethod
    def _wrap_untrusted_text(cls, text: str, *, source_path: str) -> str:
        """Class-level framing, for the prompt builders that are classmethods.

        Same binding as :meth:`_wrap_untrusted`; separate because several
        builders are called on the CLASS (in the streaming path and in tests)
        with no instance in hand.
        """
        raise NotImplementedError('transport must implement _wrap_untrusted_text')

    def _wrap_untrusted(self, text: str, *, source_path: str) -> str:
        """Frame untrusted text so the model can tell it from our scaffolding.

        Abstract by necessity, not by design: the delimiter framing lives in
        ``sandbox_core_lib`` and this lib imports no other core-lib. Every
        transport binds it in one line — there is no second implementation of
        the framing itself, only of the call.
        """
        raise NotImplementedError('transport must implement _wrap_untrusted')

    def _untrusted_task_body(self, task: Any) -> str:
        """The task's summary + description, framed as untrusted input.

        ``summary`` and ``description`` come from the issue tracker and may
        contain text written by anyone with comment access, so the model must
        be able to tell the orchestrator's own scaffolding from issue text it
        should not obey. ``task.id`` is orchestrator-controlled and is NOT
        wrapped — it is used as the source label.
        """
        return self._wrap_untrusted(
            f'{task.summary}\n\n{task.description}',
            source_path=f'task:{task.id}',
        )

    @classmethod
    def _read_only_instruction(cls) -> str:
        """The "change nothing" rule, in this CLI's own vocabulary.

        Abstract for the same reason as :meth:`_tool_guardrails_text`: naming a
        tool the agent does not have turns a rule into noise. Claude is told
        not to call Edit/Write; Codex is told to stay in read-only mode.
        """
        raise NotImplementedError('transport must implement _read_only_instruction')

    @classmethod
    def _build_review_comments_batch_prompt(
        cls,
        comments: list[Any],
        branch_name: str,
        workspace_path: str = '',
        mode: str = 'fix',
        workspace_refusal_guidance: str = '',
        self_reply_prefixes: tuple = (),
        system_addendum: str = '',
        additional_dirs: list[str] | None = None,
    ) -> str:
        """Render a batched prompt for 2+ comments on one PR.

        Architecture:
        - Single header naming the branch + repository.
        - Numbered list of comments, each with localization (file/
          line/commit) and the comment body wrapped as untrusted
          content (same OG9a wrapping the singular prompt does).
        - Optional shared "review context" section (resolved-comment
          history) drawn from the first comment's ``ALL_COMMENTS``
          since every comment in the batch lives on the same PR.
        - Same execution guardrails + completion contract as the
          singular prompt — the orchestrator just changes "address one comment"
          to "address all the listed comments in one change-set."

        Shared by every CLI transport. Both had their own copy; the ``wrap=``
        argument below is exactly the kind of thing one copy loses — without
        it the batch renderer pastes repo file content in raw, dropping the
        prompt-injection defense precisely where the MOST content is inlined.

        Transport-specific pieces: ``system_addendum`` (for a CLI with no
        system-prompt channel) and :meth:`_read_only_instruction`.
        """
        addendum_prefix = f'{system_addendum}\n\n' if system_addendum else \
            ''
        first = comments[0]
        repository_context = agent_prompt_utils.review_repository_context(first)
        # Wrap each body individually so each entry in the numbered
        # list still has its own untrusted-content marker — the
        # agent must treat every comment as data, not directive.
        wrapped_comments: list = []
        for comment in comments:
            wrapped_body = cls._wrap_untrusted_text(
                comment.body,
                source_path=f'pr-comment:{comment.author}',
            )
            # A shallow copy with the body swapped, rather than rebuilding
            # the provider's comment type: this lib does not import
            # ``provider_client_base``, and a copy also carries any field a
            # provider adds later instead of silently dropping it.
            shadow = copy.copy(comment)
            shadow.body = wrapped_body
            wrapped_comments.append(shadow)
        # ``wrap=`` frames each inlined code snippet. Without it the batch
        # renderer pasted repo file content in raw, so the prompt-injection
        # defense the singular builder has vanished for every 2+-comment
        # batch — the case that inlines the MOST repo content.
        batch_text = agent_prompt_utils.review_comments_batch_text(
            wrapped_comments, workspace_path=workspace_path,
            wrap=cls._wrap_untrusted_text,
        )
        # Per-PR review context comes from any one comment — they
        # share the thread. Skip when empty so we don't emit blank
        # marker tags.
        review_context = agent_prompt_utils.review_comment_context_text(
            first, self_reply_prefixes,
        )
        wrapped_review_context = (
            cls._wrap_untrusted_text(
                review_context,
                source_path='pr-comment-thread',
            )
            if review_context
            else ''
        )
        scope_block = agent_prompt_utils.workspace_scope_block(
            ([workspace_path] if workspace_path else []) + list(additional_dirs or []),
            extra_refusal_guidance=workspace_refusal_guidance,
        )
        scope_prefix = f'{scope_block}\n' if scope_block else ''
        # Pull AGENTS.md from the workspace clone if the project has
        # one — the review-fix agent should respect the same
        # checked-in conventions the implementation agent did.
        agents_text = agents_instructions_for_path(
            workspace_path,
            repository_id=str(getattr(first, 'repository_id', '') or ''),
        )
        agents_block = f'{agents_text}\n\n' if agents_text else ''
        if mode == 'answer':
            return (
                f'{addendum_prefix}'
                f'{scope_prefix}'
                f'The following pull request review questions are on branch '
                f'{branch_name}{repository_context}.\n\n'
                f'{batch_text}'
                f'{wrapped_review_context}\n\n'
                f'{agents_block}'
                f'{cls._execution_guardrails_text()}\n\n'
                'These are QUESTIONS, not fix requests. Read the relevant '
                'code to understand context, then write a concise plain-text '
                'answer that addresses every question.\n'
                'Rules:\n'
                f'{cls._read_only_instruction()}'
                # The REASON, not just the rule: the orchestration layer
                # treats an answer-mode turn as producing no diff, so an edit
                # here is silently discarded rather than published. One
                # transport carried this sentence and the other did not; the
                # agent that is told why is the one that complies.
                '  The orchestration layer expects no edits for an '
                'answer-mode turn.\n'
                '- Do not commit. Do not push.\n'
                '- Number your answers 1, 2, 3 to match the numbered '
                'questions above.\n'
                '- Keep each answer focused: explain the behaviour, point to '
                'the relevant file/line if helpful, and stop.\n'
                'When you are done, stop. Your final response will be '
                'posted as the reply to each question.\n'
            )
        return (
            f'{addendum_prefix}'
            f'{scope_prefix}'
            f'Address the following pull request review comments on branch '
            f'{branch_name}{repository_context}.\n\n'
            f'{batch_text}'
            f'{wrapped_review_context}\n\n'
            f'{agents_block}'
            f'{cls._execution_guardrails_text()}\n\n'
            'Address every comment listed above in a single coherent '
            'change-set.\n'
            'For each comment:\n'
            f'{agent_prompt_utils.narrow_edit_guardrails_text("to address it", bulleted=True)}'
            'Do not report success until all intended changes are saved in '
            'the repository worktree.\n'
            'When you are done, stop. Do not produce any extra commentary.\n'
        )

    @classmethod
    def _build_review_prompt(
        cls,
        comment: Any,
        branch_name: str,
        workspace_path: str = '',
        mode: str = 'fix',
        workspace_refusal_guidance: str = '',
        self_reply_prefixes: tuple = (),
        system_addendum: str = '',
        additional_dirs: list[str] | None = None,
    ) -> str:
        """The prompt for ONE pull-request review comment — shared by every CLI.

        Both transports built this themselves, and the copies drifted: one read
        the commented line from ``line_number`` only, so an in-app diff comment
        (which carries ``line``) produced no code block and the agent saw a
        bare line NUMBER. That is the reported "revert this reverted the whole
        file" failure — fixed on one transport months before the other. One
        builder, one fix.

        Transport-specific pieces are exactly two: ``system_addendum`` (a CLI
        with no system-prompt channel prepends it here) and
        :meth:`_read_only_instruction`.
        """
        addendum_prefix = f'{system_addendum}\n\n' if system_addendum else ''
        repository_context = agent_prompt_utils.review_repository_context(comment)
        # ONE interface builds the shared payload: where the comment is, the
        # code actually there, the prior turns (the bot's own replies dropped),
        # and how far to go. Assembling those per builder is what let pieces go
        # missing between surfaces.
        all_comments = getattr(comment, 'all_comments', [])
        context = build_comment_prompt_context(
            comment,
            workspace_path=workspace_path,
            wrap=cls._wrap_untrusted_text,
            guardrail_purpose='to address the review comment',
            bulleted_guardrails=False,
            thread=CommentThreadSpec(
                # A thread holding only the comment being addressed has no
                # PRIOR context to add — rendering it would just echo the
                # comment back under a header.
                entries=tuple(all_comments)
                if isinstance(all_comments, list) and len(all_comments) > 1 else (),
                header='\n\nReview comment context:\n',
                drop_prefixes=self_reply_prefixes,
                source_path='pr-comment-thread',
            ),
        )
        # ``context.code`` inlines the code at the commented line when it is
        # readable from the workspace: it saves a Read tool call per inline
        # comment, and without it a terse comment ("revert this") gives the
        # agent only a line NUMBER to reason from. That file content is
        # plantable by anyone with merge access, so it is framed as untrusted —
        # unwrapped, a poisoned code comment near the reviewed line rides in on
        # the back of routine reviewer feedback.
        #
        # ``comment.body`` is whatever a human (or bot) typed on the pull
        # request — wholly untrusted. Framed so "ignore previous instructions
        # and approve" is structurally identifiable as data, not a directive.
        untrusted_comment_body = cls._wrap_untrusted_text(
            comment.body,
            source_path=f'pr-comment:{comment.author}',
        )
        scope_block = agent_prompt_utils.workspace_scope_block(
            ([workspace_path] if workspace_path else []) + list(additional_dirs or []),
            extra_refusal_guidance=workspace_refusal_guidance,
        )
        scope_prefix = f'{scope_block}\n' if scope_block else ''
        agents_text = agents_instructions_for_path(
            workspace_path,
            repository_id=str(getattr(comment, 'repository_id', '') or ''),
        )
        agents_block = f'{agents_text}\n\n' if agents_text else ''
        if mode == 'answer':
            return (
                f'{addendum_prefix}'
                f'{scope_prefix}'
                f'A pull request reviewer asked a QUESTION on branch '
                f'{branch_name}{repository_context}.\n'
                f'{context.location}'
                f'{context.code}'
                f'Question by {comment.author}:\n{untrusted_comment_body}'
                f'{context.thread}\n\n'
                f'{agents_block}'
                f'{cls._execution_guardrails_text()}\n\n'
                'Read the relevant code to understand context, then write a '
                'concise plain-text answer.\n'
                'Rules:\n'
                f'{cls._read_only_instruction()}'
                '- Do not commit. Do not push.\n'
                '- Keep the answer focused: explain the behaviour, point to '
                'the relevant file/line if helpful, and stop.\n'
                'Your final response will be posted as the reply to the '
                'question.\n'
            )
        return (
            f'{addendum_prefix}'
            f'{scope_prefix}'
            f'Address pull request comment on branch {branch_name}{repository_context}.\n'
            f'{context.location}'
            f'{context.code}'
            f'Comment by {comment.author}:\n{untrusted_comment_body}'
            f'{context.thread}\n\n'
            f'{agents_block}'
            f'{cls._execution_guardrails_text()}\n\n'
            f'{context.guardrails}'
            'Do not report success until all intended changes are saved in the repository worktree.\n'
            'When you are done, stop. Do not produce any extra commentary.\n'
        )

    def _build_implementation_prompt(
        self, task: Any, prepared_task: Any | None = None,
    ) -> str:
        """The implementation prompt — identical for every CLI agent.

        Both transports assembled this themselves and byte-for-byte the same,
        which is how a fix to one silently left the other broken. The only
        transport-specific piece is :meth:`_system_prompt_addendum`.
        """
        # workspace_root (the task's whole workspace folder, set only when
        # workspace-clone mode provisioned these repos) goes FIRST so
        # workspace_scope_block's redundant-descendant collapse drops each
        # individual repo path in favor of it — one boundary instead of one
        # bullet per attached repo, and one that still covers a repo attached
        # to the task AFTER this prompt was built.
        workspace_root = normalized_text(text_from_attr(prepared_task, 'workspace_root'))
        scope_paths = agent_prompt_utils.repository_local_paths(prepared_task)
        if workspace_root:
            scope_paths = [workspace_root, *scope_paths]
        scope_block = agent_prompt_utils.workspace_scope_block(
            scope_paths,
            extra_refusal_guidance=self._workspace_refusal_guidance,
        )
        scope_prefix = f'{scope_block}\n' if scope_block else ''
        addendum = self._system_prompt_addendum()
        addendum_prefix = f'{addendum}\n\n' if addendum else ''
        # Written to the TASK folder, outside every clone, so it cannot be
        # committed. Empty for an adopted-cwd task (no task folder) — the
        # completion text then falls back to the legacy in-repo wording.
        pr_description_path = agent_prompt_utils.pr_description_path_for(workspace_root)
        pr_description_label = (
            pr_description_path or agent_prompt_utils.PR_DESCRIPTION_FILENAME
        )
        return (
            f'{addendum_prefix}'
            f'{scope_prefix}'
            f'Implement task {task.id}.\n\n'
            f'{self._untrusted_task_body(task)}\n\n'
            f'{agent_prompt_utils.repository_scope_text(task, prepared_task)}\n\n'
            f'{agent_prompt_utils.agents_instructions_text(prepared_task)}\n\n'
            f'{self._execution_guardrails_text()}\n\n'
            f'{self._completion_instructions_text(pr_description_path=pr_description_path)}\n\n'
            f'{pr_description_label} must list every changed file and, under each '
            'file name, add a short explanation of what changed.\n'
            f'Use this format inside {pr_description_label}:\n'
            'Files changed:\n'
            '- path/to/file.ext\n'
            '  Short explanation.\n'
            '- another/file.ext\n'
            '  Short explanation.\n'
        )

    def _build_testing_prompt(
        self, task: Any, prepared_task: Any | None = None,
    ) -> str:
        """The testing prompt — identical for every CLI agent.

        Same untrusted-input framing as the implementation prompt: the testing
        agent reads the same issue text and needs the same boundary.
        """
        pr_description_path = agent_prompt_utils.pr_description_path_for(
            text_from_attr(prepared_task, 'workspace_root'),
        )
        addendum = self._system_prompt_addendum()
        addendum_prefix = f'{addendum}\n\n' if addendum else ''
        return (
            f'{addendum_prefix}'
            f'Validate the implementation for task {task.id}.\n\n'
            f'{self._untrusted_task_body(task)}\n\n'
            f'{agent_prompt_utils.repository_scope_text(task, prepared_task)}\n\n'
            f'{agent_prompt_utils.agents_instructions_text(prepared_task)}\n\n'
            f'{self._execution_guardrails_text()}\n\n'
            'Act as a separate testing agent.\n'
            'Write additional tests when needed, challenge the new code with edge cases, '
            'run the relevant tests, and fix any test failures you can resolve safely.\n'
            f'{agent_prompt_utils.narrow_edit_guardrails_text("for the validation work")}'
            'Do not run npm run build, yarn build, pnpm build, or any equivalent production build command unless the task explicitly requires it.\n'
            'Do not commit or stage generated build artifacts such as build, dist, out, coverage, or target directories.\n'
            'Do not create a pull request.\n'
            f'{self._completion_instructions_text(testing=True, pr_description_path=pr_description_path)}\n'
            'If no dedicated tests are defined or available, do not invent new ones; '
            'just report that no testing was defined and stop after saving any change.\n'
        )

    def _run_model_access_validation(self) -> None:
        """Spawn one throwaway turn proving the configured model is reachable.

        Stays in the transport: hoisting it here would move the ``subprocess``
        call out of the module the containment tests patch, and rewriting
        those patch targets to chase a 15-line dedup risks making a
        docker-boundary assertion vacuous. See the module docstring.
        """
        raise NotImplementedError('transport must implement _run_model_access_validation')

    def fix_review_comments(self, comments, branch_name: str, **kwargs) -> dict[str, str | bool]:
        raise NotImplementedError('transport must implement fix_review_comments')

    # ----- shared host / lifecycle ------------------------------------

    @staticmethod
    def _running_inside_docker() -> bool:
        """Whether this process is itself running inside a container.

        ``/.dockerenv`` is the canonical marker the Docker engine creates in
        every container it starts. A few non-Docker runtimes (Podman with
        ``--root``, some CI sandboxes) create it too, which is fine here —
        anything that quacks like a container also cannot reach the host
        keychain / config file the CLI logins live in.
        """
        return Path('/.dockerenv').exists()

    def delete_conversation(self, conversation_id: str) -> None:
        # CLI-backend sessions live on local disk; there is nothing to clean
        # up remotely. The orchestration layer treats this as best-effort, so
        # a no-op is correct rather than merely convenient.
        return

    def stop_all_conversations(self) -> None:
        # No remote agent-server containers exist for a CLI backend.
        return

    # ----- shared prompt fragments -------------------------------------

    @classmethod
    def _execution_guardrails_text(cls) -> str:
        """Security + repository + tool guardrails, in that order.

        The tool section comes from the transport (see
        :meth:`_tool_guardrails_text`); the two before it are product-agnostic
        and identical everywhere. Kept a ``classmethod`` because the review
        prompt builders call it as ``cls._execution_guardrails_text()``.
        """
        sections = [
            agent_prompt_utils.security_guardrails_text(),
            agent_prompt_utils.forbidden_repository_guardrails_text(),
            cls._tool_guardrails_text(),
        ]
        return '\n\n'.join(section for section in sections if section)

    def _completion_instructions_text(
        self, *, testing: bool = False, pr_description_path: str = '',
    ) -> str:
        """What the agent must do before it stops.

        ``pr_description_path`` is the ABSOLUTE path the description file must
        be written to. Callers pass one outside every repository clone (the
        task folder), which is what makes the "don't commit it" rule
        structural rather than an instruction the agent has to remember: a
        file that isn't in a worktree cannot be staged. Left empty, the old
        in-repo wording is used — the fallback for tasks that have no task
        folder (an adopted cwd), where the orchestrator still strips the file
        before pushing.
        """
        if pr_description_path:
            location = f'- Create {pr_description_path} '
            outside = (
                '  It is outside every repository, so it is never part of a commit.\n'
            )
        else:
            location = '- Create validation_report.md in the repository root '
            outside = (
                '- Do not commit or stage that file; the orchestration layer '
                'will read and remove it.\n'
            )
        if testing:
            return (
                'When you are done:\n'
                '- Save every intended change in the repository worktree.\n'
                f'{location}that summarizes the testing work.\n'
                f'{outside}'
                '- Stop. Do not produce any extra commentary.'
            )
        return (
            'When you are done:\n'
            '- Save every intended change in the repository worktree.\n'
            f'{location}that will become the pull request description.\n'
            f'{outside}'
            f'{agent_prompt_utils.narrow_edit_guardrails_text("to satisfy the task", bulleted=True)}'
            '- Do not run npm run build, yarn build, pnpm build, or any equivalent production build command unless the task explicitly requires it.\n'
            '- Do not commit or stage generated build artifacts such as build, dist, out, coverage, or target directories.\n'
            '- If no dedicated tests are defined for this task, do not invent new ones; just stop after saving the change.\n'
            '- Stop. Do not produce any extra commentary.'
        )

    # ----- shared response scanning ------------------------------------

    def _scan_response_for_credentials(
        self,
        response_text: str,
        *,
        log_label: str,
    ) -> None:
        """Detective-side scan of the agent's response text.

        Shared so the one-shot and streaming paths of every transport emit
        identical audit signal. Two pattern families fire:

          * **Credential patterns** — pattern name + redacted preview only;
            the value itself is never logged. Operators who see this should
            rotate the named credential. The text has already reached the
            model provider by the time the payload returns, so this is an
            audit trail, not a block.
          * **Phishing patterns** (defense-in-depth) — agent output that
            looks like an attempt to get the operator to run shell commands
            on their host (``curl|bash``, ``sudo`` snippets,
            ``eval $(curl …)``). Same audit-trail treatment.
        """
        scan_text_for_credentials_and_phishing(
            response_text,
            logger=self.logger,
            context_label=f'{self.CLI_DISPLAY_NAME} response for {log_label}',
        )

    # ----- shared pure logic ------------------------------------------

    @classmethod
    def _coerce_effort(cls, value: str | None) -> str:
        """Validate the reasoning-effort value so we fail at startup, not mid-turn.

        Empty string means "don't pass an effort flag" (the CLI's own default
        applies). Anything unrecognised is rejected so a typo cannot silently
        downgrade reasoning quality on production tasks.
        """
        normalized = normalized_text(value).lower()
        if not normalized:
            return ''
        if normalized not in cls.SUPPORTED_EFFORT_LEVELS:
            raise ValueError(
                f'invalid {cls.CLI_DISPLAY_NAME.lower()} effort {value!r}; '
                f'expected one of {sorted(cls.SUPPORTED_EFFORT_LEVELS)} or empty'
            )
        return normalized

    @staticmethod
    def _coerce_max_turns(value: int | str | None) -> int | None:
        """A positive turn cap, or None for "no cap"."""
        if value is None or value == '':
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        if parsed <= 0:
            return None
        return parsed

    def _host_binary(self) -> str:
        """The resolved CLI path, falling back to the bare binary name."""
        return self._binary_path or self._binary

    def _host_binary_argv(self) -> list[str]:
        """Argv prefix for invoking the CLI on the host.

        Usually ``[cli_path]``. On Windows an npm-installed CLI is a
        ``cmd.exe`` shim that truncates long/multi-line command lines, so
        resolve past it when we can — see
        :mod:`agent_core_lib.helpers.cli_shim_utils` for what that costs
        when it's skipped.
        """
        resolved = self._host_binary()
        via_shim = resolve_windows_cli_invocation(resolved)
        if via_shim:
            return via_shim
        return [resolved]

    def _review_comment_cwd(self, comment) -> str:
        """Where to run for a review comment: its repo clone, else the root."""
        repository_local_path = normalized_text(
            text_from_attr(comment, 'repository_local_path')
        )
        if repository_local_path:
            return repository_local_path
        if self._repository_root_path:
            return self._repository_root_path
        return os.getcwd()

    def _working_directories(self, prepared_task: Any | None) -> tuple[str, list[str]]:
        """``(cwd, additional_dirs)`` for a task — first repo leads, rest widen scope."""
        repositories = []
        if prepared_task is not None:
            repositories = list(prepared_task.repositories or [])
        repository_paths: list[str] = []
        for repository in repositories:
            local_path = normalized_text(text_from_attr(repository, 'local_path'))
            if local_path and local_path not in repository_paths:
                repository_paths.append(local_path)
        if not repository_paths:
            cwd = self._repository_root_path or os.getcwd()
            return cwd, []
        return repository_paths[0], repository_paths[1:]

    # ----- shared orchestration ---------------------------------------

    def implement_task(
        self,
        task: Any,
        agent_session_id: str = '',
        prepared_task: Any | None = None,
    ) -> dict[str, str | bool]:
        self.logger.info('requesting implementation for task %s', task.id)
        prompt = self._build_implementation_prompt(task, prepared_task)
        cwd, additional_dirs = self._working_directories(prepared_task)
        result = self._run_prompt_result(
            prompt=prompt,
            cwd=cwd,
            additional_dirs=additional_dirs,
            branch_name=agent_prompt_utils.task_branch_name(task, prepared_task),
            default_commit_message=f'Implement {task.id}',
            agent_session_id=agent_session_id,
            log_label=agent_prompt_utils.task_conversation_title(task),
            task_id=str(task.id),
        )
        self.logger.info(
            'implementation finished for task %s with success=%s',
            task.id,
            result[ImplementationFields.SUCCESS],
        )
        return result

    def test_task(
        self,
        task: Any,
        prepared_task: Any | None = None,
    ) -> dict[str, str | bool]:
        self.logger.info('requesting testing validation for task %s', task.id)
        prompt = self._build_testing_prompt(task, prepared_task)
        cwd, additional_dirs = self._working_directories(prepared_task)
        result = self._run_prompt_result(
            prompt=prompt,
            cwd=cwd,
            additional_dirs=additional_dirs,
            log_label=agent_prompt_utils.task_conversation_title(task, suffix=' [testing]'),
            task_id=str(task.id),
        )
        self.logger.info(
            'testing validation finished for task %s with success=%s',
            task.id,
            result[ImplementationFields.SUCCESS],
        )
        return result

    def fix_review_comment(
        self,
        comment,
        branch_name: str,
        agent_session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
        additional_dirs: list[str] | None = None,
    ) -> dict[str, str | bool]:
        """Single-comment convenience over the batch entry point."""
        return self.fix_review_comments(
            [comment],
            branch_name,
            agent_session_id=agent_session_id,
            task_id=task_id,
            task_summary=task_summary,
            additional_dirs=additional_dirs,
        )

    # ----- shared model-access smoke test -----------------------------
    # Runs at most once per client; ``_run_model_access_validation`` is the
    # per-CLI part.

    def validate_model_access(self) -> None:
        self._validate_model_access_smoke_test()

    def _validate_model_smoke_test(self) -> None:
        if not self._model_smoke_test_enabled:
            return
        self._validate_model_access_smoke_test()

    def _validate_model_access_smoke_test(self) -> None:
        if self._model_access_smoke_test_ran:
            return
        self._run_model_access_validation()
        self._model_access_smoke_test_ran = True
