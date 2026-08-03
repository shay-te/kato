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
from typing import Any

from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from agent_core_lib.agent_core_lib.helpers import agent_prompt_utils
from agent_core_lib.agent_core_lib.helpers.cli_shim_utils import (
    resolve_windows_cli_invocation,
)
from utils_core_lib.utils_core_lib.text_utils import (
    normalized_text,
    text_from_attr,
)


class CliAgentSharedBehaviour(object):
    """Mixin: the transport-agnostic half of a CLI agent client."""

    # ----- hooks the concrete transport MUST provide -------------------
    # Abstract on purpose. See the module docstring: a defaulted hook here
    # would let a transport silently lose its sandbox or its prompts.

    def _run_prompt_result(self, **kwargs) -> dict[str, str | bool]:
        """Spawn the CLI and shape the result. Owns the sandbox wrap."""
        raise NotImplementedError('transport must implement _run_prompt_result')

    def _build_implementation_prompt(self, task: Any, prepared_task: Any | None = None) -> str:
        raise NotImplementedError('transport must implement _build_implementation_prompt')

    def _build_testing_prompt(self, task: Any, prepared_task: Any | None = None) -> str:
        raise NotImplementedError('transport must implement _build_testing_prompt')

    def _run_model_access_validation(self) -> None:
        raise NotImplementedError('transport must implement _run_model_access_validation')

    def fix_review_comments(self, comments, branch_name: str, **kwargs) -> dict[str, str | bool]:
        raise NotImplementedError('transport must implement fix_review_comments')

    # ----- shared pure logic ------------------------------------------

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
