"""Behaviour shared by every one-process-per-turn CLI agent client.

Codex, Grok and OpenCode are driven the same way: one non-interactive process
per prompt, a JSON event stream on stdout, the session resumed by id. What
differs between them is the argv and the names of their events; everything
else in a one-shot client was the same code three times.

This sits on top of ``CliAgentSharedBehaviour`` and keeps its split, drawn at
the SPAWN boundary: everything here is orchestration and pure logic that never
starts a process. The transport keeps every ``subprocess``/``shutil`` call —
``validate_connection``, ``_run_prompt`` (with the sandbox wrap readable at the
site that uses it) and ``_run_model_access_validation`` — so containment tests
keep patching the module that actually spawns.

Required of the concrete transport, beyond ``CliAgentSharedBehaviour``'s list:
  attributes ``DEFAULT_BINARY``, ``CLI_DISPLAY_NAME``, ``SUPPORTED_EFFORT_LEVELS``,
             ``BYPASS_WARNING``, ``IGNORED_KNOBS`` (+ ``IGNORED_KNOBS_HINT``)
  methods   ``_build_command``, ``_translate_wire_event``
"""

from __future__ import annotations

import os
import subprocess
import threading
from typing import Any

from agent_core_lib.agent_core_lib.cli_agent_shared import CliAgentSharedBehaviour
from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from agent_core_lib.agent_core_lib.helpers import agent_prompt_utils
from agent_core_lib.agent_core_lib.helpers.command_floor import prompt_floor_rules
from agent_core_lib.agent_core_lib.helpers.logging_utils import configure_logger
from agent_core_lib.agent_core_lib.session.turn_result import (
    parse_json_event,
    turn_result,
)
from utils_core_lib.utils_core_lib.text_utils import condensed_text, normalized_text


class PerTurnCliAgentBehaviour(CliAgentSharedBehaviour):
    """Mixin: the spawn-free half of a per-turn CLI agent client."""

    DEFAULT_BINARY = ''
    DEFAULT_TIMEOUT_SECONDS = 1800
    SMOKE_TEST_PROMPT = 'Reply with exactly: ok. Do not call any tools.'
    SMOKE_TEST_TIMEOUT_SECONDS = 120
    VERSION_PROBE_TIMEOUT_SECONDS = 30
    SUPPORTED_EFFORT_LEVELS: frozenset[str] = frozenset()
    #: Logged once at construction when bypass is on. Names the CLI's own
    #: flag, so it is the transport's to write.
    BYPASS_WARNING = ''
    #: Constructor knobs this CLI has no flag for. They are accepted so the
    #: factory calls every client with the same kwargs, and named in one info
    #: line so an operator who set them is not left guessing.
    IGNORED_KNOBS: tuple[str, ...] = ()
    IGNORED_KNOBS_HINT = ''
    #: Variables added to the inherited environment of every spawn.
    SUBPROCESS_ENV: dict[str, str] = {}

    def __init__(
        self,
        *,
        binary: str = '',
        model: str = '',
        max_turns: int | str | None = None,
        allowed_tools: str = '',
        disallowed_tools: str = '',
        bypass_permissions: bool = False,
        docker_mode_on: bool = False,
        read_only_tools_on: bool = False,
        max_retries: int = 3,
        timeout_seconds: int = 1800,
        repository_root_path: str = '',
        model_smoke_test_enabled: bool = False,
        extra_args: list[str] | None = None,
        effort: str = '',
        lessons_path: str = '',
        workspace_refusal_guidance: str = '',
        self_reply_prefixes: tuple = (),
    ) -> None:
        self.max_retries = max(1, int(max_retries or 1))
        self._binary = normalized_text(binary) or self.DEFAULT_BINARY
        self._binary_path = ''
        self._model = normalized_text(model)
        self._max_turns = self._coerce_max_turns(max_turns)
        self._effort = self._coerce_effort(effort)
        self._bypass_permissions = bool(bypass_permissions)
        self._docker_mode_on = bool(docker_mode_on)
        self._read_only_tools_on = bool(read_only_tools_on)
        self._allowed_tools = normalized_text(allowed_tools)
        self._disallowed_tools = normalized_text(disallowed_tools)
        self._timeout_seconds = max(
            60, int(timeout_seconds or self.DEFAULT_TIMEOUT_SECONDS),
        )
        self._repository_root_path = normalized_text(repository_root_path)
        self._model_smoke_test_enabled = bool(model_smoke_test_enabled)
        self._model_access_smoke_test_ran = False
        self._extra_args = list(extra_args or [])
        self._lessons_path = normalized_text(lessons_path)
        # Product-specific refusal guidance appended to the generic workspace
        # scope block; supplied by the spawner ('' otherwise).
        self._workspace_refusal_guidance = workspace_refusal_guidance or ''
        # The host bot's review-reply prefixes — its own prior replies are
        # dropped from review-comment context. Empty = no filtering.
        self._self_reply_prefixes = tuple(self_reply_prefixes or ())
        self.logger = configure_logger(self.__class__.__name__)
        if self._bypass_permissions:
            self.logger.warning(self.BYPASS_WARNING)
        if any(self._knob_is_set(knob) for knob in self.IGNORED_KNOBS):
            self.logger.info(
                f'{self.CLI_DISPLAY_NAME} backend ignores '
                f'{" / ".join(self.IGNORED_KNOBS)} — {self.IGNORED_KNOBS_HINT}'
            )

    def _knob_is_set(self, knob: str) -> bool:
        if knob == 'max_turns':
            return self._max_turns is not None
        return bool(getattr(self, f'_{knob}', ''))

    # ----- hooks the concrete transport MUST provide ---------------------

    def _build_command(self, **kwargs) -> list[str]:
        """The argv for one non-interactive run of the CLI."""
        raise NotImplementedError('transport must implement _build_command')

    def _translate_wire_event(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """The CLI's own event as events in the shared vocabulary (``turn_events``)."""
        raise NotImplementedError('transport must implement _translate_wire_event')

    # ----- public agent-client API (parity with the other transports) -----

    @property
    def model(self) -> str:
        """The configured model ('' = the CLI's own default)."""
        return self._model

    def investigate(
        self,
        prompt: str,
        *,
        cwd: str = '',
        additional_dirs: list[str] | None = None,
        sandbox_root: str = '',
        task_id: str = '',
        log_label: str = '',
        cancel_event: threading.Event | None = None,
        timeout_seconds: int = 0,
        model: str = '',
    ) -> str:
        """Read-only single turn — triage, and an independent review.

        The read-only mode is passed to THIS run (``sandbox_override``, which
        the transport's ``_build_command`` ranks above bypass) instead of
        flipping the shared client's bypass flag for the length of the call,
        which a concurrent run could observe.

        ``sandbox_root`` is accepted for parity with the persistent-process
        client; a multi-repo caller hands the run what it needs to read.
        ``cancel_event`` makes the run stoppable (``ProcessCancelled``).
        """
        del sandbox_root  # see docstring
        normalized_prompt = normalized_text(prompt)
        if not normalized_prompt:
            raise ValueError('prompt is required to run an investigation')
        normalized_cwd = normalized_text(cwd)
        if not normalized_cwd:
            normalized_cwd = self._repository_root_path or os.getcwd()
        payload = self._run_prompt(
            prompt=normalized_prompt,
            cwd=normalized_cwd,
            additional_dirs=list(additional_dirs or []),
            log_label=log_label or 'triage investigation',
            task_id=task_id or 'triage',
            sandbox_override='read-only',
            cancel_event=cancel_event,
            timeout_seconds=timeout_seconds,
            model=model,
        )
        result_text = payload.get('result') or payload.get(ImplementationFields.MESSAGE) or ''
        return str(result_text)

    def fix_review_comments(
        self,
        comments: list[Any],
        branch_name: str,
        agent_session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
        mode: str = 'fix',
        additional_dirs: list[str] | None = None,
    ) -> dict[str, str | bool]:
        """Address one or more PR review comments in a single spawn.

        Same signature, return contract and ``mode`` semantics (``fix`` vs
        ``answer``) as every other client.
        """
        if not comments:
            raise ValueError('fix_review_comments requires at least one comment')
        cwd = self._review_comment_cwd(comments[0])
        extra_dirs = [
            path for path in (
                normalized_text(path) for path in (additional_dirs or [])
            )
            if path and path != cwd
        ]
        # These CLIs have no system-prompt flag, so every prompt builder must
        # splice the addendum (lessons, the untrusted-content explanation) into
        # the prompt text itself — a review run without it would not know what
        # the UNTRUSTED_WORKSPACE_FILE tags around a comment body mean.
        system_addendum = self._system_prompt_addendum()
        builder = (
            self._build_review_prompt if len(comments) == 1
            else self._build_review_comments_batch_prompt
        )
        subject = comments[0] if len(comments) == 1 else comments
        prompt = builder(
            subject, branch_name, workspace_path=cwd, mode=mode,
            workspace_refusal_guidance=self._workspace_refusal_guidance,
            self_reply_prefixes=self._self_reply_prefixes,
            system_addendum=system_addendum,
            additional_dirs=extra_dirs,
        )
        result = self._run_prompt_result(
            prompt=prompt,
            cwd=cwd,
            additional_dirs=extra_dirs,
            agent_session_id=agent_session_id,
            branch_name=branch_name,
            default_commit_message='Address review comments',
            log_label=agent_prompt_utils.review_conversation_title(
                comments[0],
                task_id=task_id,
                task_summary=task_summary,
            ),
            task_id=task_id,
        )
        self.logger.info(
            'review fix finished for pull request %s with %d comment(s) success=%s',
            comments[0].pull_request_id,
            len(comments),
            result[ImplementationFields.SUCCESS],
        )
        return result

    # ----- prompt fragments ------------------------------------------------

    @classmethod
    def _read_only_instruction(cls) -> str:
        """These CLIs expose generic tooling, so the rule names the MODE."""
        return '- Do NOT modify any files. Stay in read-only mode.\n'

    @staticmethod
    def _tool_guardrails_text() -> str:
        # No allow/deny tool-list flag on these CLIs, so the prompt-level rules
        # are the only layer that says "no git". A workspace sandbox limits
        # where writes land but does NOT block ``git`` shell calls inside it.
        return (
            'Tool guardrails:\n'
            '- Use edit/write/read tooling for file edits and reads.\n'
            '- Use the shell sparingly and only for non-destructive needs (rg, sed -n, cat, ls).\n'
            '\n'
            # Rendered from the SAME floor the deny-flag transports enforce, so
            # this prompt cannot quietly name a different set.
            f'{prompt_floor_rules()}'
            '\n'
            'YOUR JOB IS TO EDIT FILES. THAT IS ALL.\n'
            '\n'
            'You do NOT do any of the following — ever, under any circumstance:\n'
            '- git (status, diff, log, add, commit, push, pull, fetch, checkout, switch, branch, reset, rebase, stash, tag, anything)\n'
            '- create pull requests / merge requests\n'
            '- call GitHub / GitLab / Bitbucket / Azure DevOps APIs\n'
            '- ask the operator for permission to commit\n'
            '- mention git, commits, PRs, or branches in your reply except to say you are done editing\n'
            '\n'
            'The orchestrator handles everything after you finish:\n'
            '- The orchestrator that spawned you owns the git lifecycle.\n'
            '- It sees your file edits on disk and commits them.\n'
            '- It pushes the branch.\n'
            '- It opens the pull request.\n'
            '- This is automatic. The operator does NOT need to allow anything, run anything, or click anything for git to happen.\n'
            '\n'
            'When you finish editing, your reply must be exactly one short sentence: "Done — edits written, the orchestrator will publish."  If you genuinely have nothing more to say, that one line is the entire reply.\n'
            '\n'
            'Do NOT say things like "I am ready to commit when you allow git access" or "let me know when I can push" or any variation. Those are wrong because there is nothing for the operator to allow — the orchestrator runs git automatically the moment your turn ends.'
        )

    # ----- results ---------------------------------------------------------

    def _build_subprocess_env(self) -> dict[str, str]:
        """The operator's environment (auth, config dirs) plus ``SUBPROCESS_ENV``."""
        env = os.environ.copy()
        env.update(self.SUBPROCESS_ENV)
        return env

    def _parse_jsonl_payload(self, stdout: str) -> dict[str, object]:
        """The run's JSONL stream, translated, reduced to the host's payload."""
        events: list[dict[str, Any]] = []
        for raw_line in str(stdout or '').splitlines():
            raw = parse_json_event(raw_line)
            if raw is not None:
                events.extend(self._translate_wire_event(raw))
        return turn_result(events)

    def _parse_completed_process(
        self,
        completed: subprocess.CompletedProcess,
        *,
        log_label: str,
        last_message_file: str = '',
    ) -> dict[str, str | bool]:
        stdout = completed.stdout or ''
        stderr = (completed.stderr or '').strip()
        payload = self._parse_jsonl_payload(stdout)
        # A CLI that writes its final reply to a file (Codex's
        # ``--output-last-message``) is believed over the event stream: the
        # file is the documented contract, event names drift between versions.
        file_message = ''
        if last_message_file:
            try:
                with open(last_message_file, 'r', encoding='utf-8') as handle:
                    file_message = handle.read().strip()
            except OSError:
                file_message = ''
        is_error = bool(payload.get('is_error', False))
        success = completed.returncode == 0 and not is_error
        # The parsed error event is operator-readable ("model X not
        # supported"); raw stderr is often the CLI's own bookkeeping noise.
        parsed_error_text = normalized_text(payload.get('result', '')) if is_error else ''
        result_text = file_message or normalized_text(payload.get('result', ''))
        session_id_value = normalized_text(payload.get(ImplementationFields.AGENT_SESSION_ID, ''))
        name = self.CLI_DISPLAY_NAME
        if completed.returncode != 0:
            detail = parsed_error_text or stderr or condensed_text(stdout) or 'no output'
            self.logger.error(
                '%s CLI returned exit code %s for %s: %s',
                name, completed.returncode, log_label, detail,
            )
            raise RuntimeError(
                f'{name} CLI exited with status {completed.returncode}: {detail}'
            )
        if is_error:
            detail = parsed_error_text or stderr or f'unknown {name} CLI error'
            raise RuntimeError(f'{name} CLI reported an error: {detail}')
        self._scan_response_for_credentials(result_text, log_label=log_label)
        result: dict[str, str | bool] = {
            ImplementationFields.SUCCESS: success,
            'summary': result_text,
        }
        if result_text:
            result[ImplementationFields.MESSAGE] = result_text
        if session_id_value:
            result[ImplementationFields.AGENT_SESSION_ID] = session_id_value
        return result

    def _smoke_test_failure(self, completed: subprocess.CompletedProcess) -> str:
        """Why a model-access smoke run failed, or '' when it passed.

        Pure: the transport spawns the run and raises with this text.
        """
        name = self.CLI_DISPLAY_NAME
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or '').strip() or 'unknown error'
            return f'{name} CLI smoke test failed: {detail}'
        payload = self._parse_jsonl_payload(completed.stdout or '')
        if payload.get('is_error'):
            detail = str(payload.get('result') or '') or f'unknown {name} CLI error'
            return f'{name} CLI smoke test reported an error: {detail}'
        return ''
