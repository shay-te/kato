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

import os
from pathlib import Path
from typing import Any

from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from agent_core_lib.agent_core_lib.helpers import agent_prompt_utils
from agent_core_lib.agent_core_lib.helpers.cli_shim_utils import (
    resolve_windows_cli_invocation,
)
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

    def _run_prompt_result(self, **kwargs) -> dict[str, str | bool]:
        """Spawn the CLI and shape the result. Owns the sandbox wrap."""
        raise NotImplementedError('transport must implement _run_prompt_result')

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

    def _build_implementation_prompt(self, task: Any, prepared_task: Any | None = None) -> str:
        raise NotImplementedError('transport must implement _build_implementation_prompt')

    def _build_testing_prompt(self, task: Any, prepared_task: Any | None = None) -> str:
        raise NotImplementedError('transport must implement _build_testing_prompt')

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
