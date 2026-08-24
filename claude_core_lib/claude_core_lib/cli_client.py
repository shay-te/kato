from __future__ import annotations

import json
import os
import shutil
import subprocess

from agent_core_lib.agent_core_lib.cli_agent_shared import CliAgentSharedBehaviour
from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from agent_core_lib.agent_core_lib.helpers import agent_prompt_utils
from agent_core_lib.agent_core_lib.helpers.command_floor import (
    FLOOR_DENY_PROGRAMS,
    GIT_MUTATING_SUBCOMMANDS,
    UNSUPERVISED_DENY_SUBCOMMANDS,
    cli_deny_patterns,
)
from agent_core_lib.agent_core_lib.helpers.logging_utils import configure_logger
from agent_core_lib.agent_core_lib.helpers.read_only_tools import (
    READ_ONLY_ALLOWED_TOOLS,
    READ_ONLY_DISALLOWED_TOOLS,
)
from agent_core_lib.agent_core_lib.helpers.session_id_utils import fix_session_id
from utils_core_lib.utils_core_lib.text_utils import (
    condensed_text,
    normalized_text,
    text_from_mapping,
)
from claude_core_lib.claude_core_lib.helpers.effort_levels import (
    FALLBACK_EFFORT_LEVELS,
)
from claude_core_lib.claude_core_lib.helpers.spawn_utils import (
    append_additional_dirs,
    append_model_effort_flags,
    build_appended_system_prompt,
    build_claude_subprocess_env,
    wrap_spawn_for_docker,
)
from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from sandbox_core_lib.sandbox_core_lib.workspace_delimiter import (
    wrap_untrusted_workspace_content,
)


class ClaudeCliClient(CliAgentSharedBehaviour):
    """Drive Anthropic's Claude Code CLI (`claude -p`) as the implementation/testing backend.

    Provides the same public interface as :class:`the agent client` so the rest of the
    orchestration layer can use either backend interchangeably. Selection is
    driven by the ``the agent-backend setting`` environment variable.
    """

    DEFAULT_BINARY = 'claude'
    CLI_DISPLAY_NAME = 'Claude'
    DEFAULT_TIMEOUT_SECONDS = 1800
    SAFE_PERMISSION_MODE = 'acceptEdits'
    BYPASS_PERMISSION_MODE = 'bypassPermissions'
    # Safe pre-approved tools. ``Agent`` (the subagent/fan-out tool, renamed
    # from ``Task`` in CLI 2.1.63) is included so the agent can spawn subagents
    # headlessly — in ``-p`` there's no prompt to grant a non-allowlisted tool,
    # so without this the agent silently can't fan out. Both names are listed
    # for version skew (``Task`` is an alias on newer CLIs). Operators can still
    # override the whole list via ``the allowed-tools setting``.
    DEFAULT_ALLOWED_TOOLS = 'Agent,Task,Edit,Write,Read,Bash,Glob,Grep'
    # The FLOOR — which git subcommands and which programs are refused, and
    # why — is shared policy in ``agent_core_lib.helpers.command_floor``, so
    # the transport that can only state it in a prompt states exactly what
    # this one enforces. Claude renders it into ``--disallowedTools``
    # patterns, which is the strongest form available: they hold even in
    # ``bypassPermissions``, where no per-tool prompt fires.
    GIT_DENY_PATTERNS = cli_deny_patterns(GIT_MUTATING_SUBCOMMANDS, program='git')
    # Action Guard "Layer A": programs with no legitimate use in a workspace.
    ACTION_GUARD_DENY_PATTERNS = cli_deny_patterns(FLOOR_DENY_PROGRAMS)
    # Withdrawn only where nobody is watching (bypassPermissions), because the
    # content-aware guard that would otherwise catch the destructive forms
    # never runs there.
    UNSUPERVISED_DENY_PATTERNS = cli_deny_patterns(
        UNSUPERVISED_DENY_SUBCOMMANDS, program='git',
    )
    SMOKE_TEST_PROMPT = 'Reply with exactly: ok. Do not call any tools.'
    SMOKE_TEST_TIMEOUT_SECONDS = 120
    VERSION_PROBE_TIMEOUT_SECONDS = 30

    # Single source of truth lives in ``helpers.effort_levels``; this
    # derives the validation set from the same fallback tuple the
    # discovery path falls back to, so the two never drift.
    SUPPORTED_EFFORT_LEVELS = frozenset(FALLBACK_EFFORT_LEVELS)

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
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        repository_root_path: str = '',
        model_smoke_test_enabled: bool = False,
        extra_args: list[str] | None = None,
        effort: str = '',
        architecture_doc_path: str = '',
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
        # Set from ``the docker setting`` at boot. When True, the
        # per-task spawns (test_task → _run_prompt, investigate →
        # _run_prompt) wrap the Claude subprocess in the hardened
        # sandbox. Boot-time validators (validate_connection,
        # _run_model_access_validation) deliberately stay on the host —
        # they have no workspace and no untrusted prompt. Independent
        # of ``bypass_permissions``: docker is containment, bypass is
        # the prompt layer.
        self._docker_mode_on = bool(docker_mode_on)
        # Set from ``the read-only-tools setting`` at boot.
        # When True (and only valid alongside docker mode — the
        # ``validate_read_only_tools_requires_docker`` startup gate
        # refuses the flag without docker), every spawn appends the
        # hardcoded ``READ_ONLY_TOOLS_ALLOWLIST`` to ``--allowedTools``
        # so the operator isn't prompted for grep / cat / ls / find /
        # head / tail / wc / file / stat / rg / Read. Mutating tools
        # (Edit, Write, Bash without an explicit pattern) still
        # prompt as today. Independent of ``bypass_permissions``;
        # bypass disables ALL prompts, this disables only the
        # read-only ones.
        self._read_only_tools_on = bool(read_only_tools_on)
        # When not bypassing, pre-approve a safe default tool list so the
        # agent does not stall asking for permission in headless `-p` mode.
        # Users can override or extend via the allowed-tools setting.
        normalized_allowed = normalized_text(allowed_tools)
        self._allowed_tools = (
            normalized_allowed
            if normalized_allowed or self._bypass_permissions
            else self.DEFAULT_ALLOWED_TOOLS
        )
        self._disallowed_tools = normalized_text(disallowed_tools)
        self._timeout_seconds = max(60, int(timeout_seconds or self.DEFAULT_TIMEOUT_SECONDS))
        self._repository_root_path = normalized_text(repository_root_path)
        self._model_smoke_test_enabled = bool(model_smoke_test_enabled)
        self._model_access_smoke_test_ran = False
        self._extra_args = list(extra_args or [])
        self._architecture_doc_path = normalized_text(architecture_doc_path)
        self._lessons_path = normalized_text(lessons_path)
        # Product-specific actionable refusal guidance appended to the
        # generic workspace scope block. Supplied by the spawner (the orchestrator)
        # so agent_core_lib/claude_core_lib stay product-agnostic; '' for
        # any consumer that doesn't set it.
        self._workspace_refusal_guidance = workspace_refusal_guidance or ''
        # Host bot's review-reply prefixes — drop the bot's own prior replies
        # from review-comment context. Empty = no filtering (agnostic default).
        self._self_reply_prefixes = tuple(self_reply_prefixes or ())
        self.logger = configure_logger(self.__class__.__name__)
        if self._bypass_permissions:
            self.logger.warning(
                'Bypass-permissions mode is enabled: Claude will run with '
                '--permission-mode bypassPermissions. Per-tool prompts are '
                'disabled — the agent can run Bash, Edit, Write, and any '
                'other tool without asking. The operator who enabled this '
                'accepts responsibility for any harm caused by the agent. '
                'See SECURITY.md.'
            )

    @property
    def _permission_mode(self) -> str:
        return (
            self.BYPASS_PERMISSION_MODE
            if self._bypass_permissions
            else self.SAFE_PERMISSION_MODE
        )

    # ----- public agent-client API (parity with the other transports) -----

    def validate_connection(self) -> None:
        if self._running_inside_docker():
            raise RuntimeError(
                'The Claude backend is not supported inside Docker. '
                'The Claude Code CLI authenticates against your host '
                '`claude login` credentials (macOS Keychain, Linux config '
                'file, or Windows Credential Manager), and the container '
                'cannot reach those. '
                'Run the agent on the host instead, or select a backend that '
                'supports containerized execution (e.g. OpenHands).'
            )
        binary_path = shutil.which(self._binary)
        if not binary_path:
            # Multi-line message printed at host startup. Lead with
            # the one-line install command (works on macOS / Linux /
            # Windows) so the operator can fix this in 30 seconds
            # without reading the docs page.
            raise RuntimeError(
                f'\n'
                f'Claude CLI ("{self._binary}") was not found on PATH.\n'
                f'\n'
                f'Install Claude Code via npm (works on macOS, Linux, and Windows):\n'
                f'\n'
                f'    npm install -g @anthropic-ai/claude-code\n'
                f'\n'
                f'Prerequisite: Node.js 18+ (https://nodejs.org/). Verify with:\n'
                f'\n'
                f'    node --version\n'
                f'    claude --version\n'
                f'\n'
                f'After install, the ``claude`` binary must be on PATH (npm puts it\n'
                f'there automatically). If you installed it somewhere else,\n'
                f'configure the Claude binary path. Full setup docs:\n'
                f'    https://docs.claude.com/en/docs/claude-code/setup\n'
            )
        self._binary_path = binary_path
        # Boot-time validator: no workspace, no untrusted prompt — runs
        # ``claude --version`` only. Sandbox-wrap is intentionally
        # skipped even when ``the docker setting=true``: nothing here for
        # the sandbox to bound, and a container spin would add ~1-2s to
        # every startup with zero security benefit.
        try:
            result = subprocess.run(
                [*self._host_binary_argv(), '--version'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                check=False,
                timeout=self.VERSION_PROBE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(
                f'Claude CLI binary "{self._binary}" failed to launch: {exc}'
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip() or 'unknown error'
            raise RuntimeError(
                f'Claude CLI binary "{self._binary}" failed to report a version: {detail}'
            )
        self.logger.info(
            'Claude CLI is available at %s (%s)',
            binary_path,
            condensed_text(result.stdout),
        )
        self._validate_model_smoke_test()

    def _wrap_untrusted(self, text: str, *, source_path: str) -> str:
        """Bind the shared prompt scaffolding to the delimiter framing."""
        return wrap_untrusted_workspace_content(text, source_path=source_path)

    @classmethod
    def _wrap_untrusted_text(cls, text: str, *, source_path: str) -> str:
        """Bind the shared prompt scaffolding to the delimiter framing."""
        return wrap_untrusted_workspace_content(text, source_path=source_path)

    @classmethod
    def _read_only_instruction(cls) -> str:
        """Claude has named tools, so the rule names the ones that mutate."""
        return (
            '- Do NOT modify any files. Do not call Edit, Write, or any '
            'tool that mutates the workspace.\n'
        )

    def investigate(self, prompt: str, *, cwd: str = '') -> str:
        """Run a single read-only Claude turn and return the raw text.

        Used by the triage flow: the orchestrator hands Claude a task description
        and a list of valid triage outcome tags, asks Claude to pick
        one. No file edits, no PR work — disallowedTools blocks all
        write paths (Edit, Write, Bash, etc.) so even a confused turn
        can't damage the repo.
        """
        normalized_prompt = normalized_text(prompt)
        if not normalized_prompt:
            raise ValueError('prompt is required to run an investigation')
        normalized_cwd = normalized_text(cwd)
        if not normalized_cwd:
            normalized_cwd = self._repository_root_path or os.getcwd()
        # Strict tool denylist: triage is read-only by definition.
        original_disallowed = self._disallowed_tools
        original_allowed = self._allowed_tools
        try:
            self._disallowed_tools = READ_ONLY_DISALLOWED_TOOLS
            self._allowed_tools = READ_ONLY_ALLOWED_TOOLS
            payload = self._run_prompt(
                prompt=normalized_prompt,
                cwd=normalized_cwd,
                additional_dirs=[],
                log_label='triage investigation',
                task_id='triage',
            )
        finally:
            self._disallowed_tools = original_disallowed
            self._allowed_tools = original_allowed
        result_text = payload.get('result') or payload.get(ImplementationFields.MESSAGE) or ''
        return str(result_text)

    def fix_review_comments(
        self,
        comments: list[ReviewComment],
        branch_name: str,
        agent_session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
        mode: str = 'fix',
        additional_dirs: list[str] | None = None,
    ) -> dict[str, str | bool]:
        """Address multiple PR review comments in a single Claude spawn.

        ``comments`` must all belong to the same pull request — the
        caller (``ReviewCommentService``) guarantees grouping by
        (repo, pr) before calling. ``branch_name`` is the existing
        task branch to commit on; one push covers every comment in
        the batch.

        ``mode``:
        - ``'fix'`` (default) — the legacy flow. Agent makes edits,
          commits, returns success when the workspace has the change.
        - ``'answer'`` — the question-answering flow. Agent reads the
          code to understand context but does NOT modify any files;
          the returned ``message`` text is what the orchestrator posts back to
          each commenter as a reply. The caller (service) skips
          ``publish_review_fix`` for this mode.

        For ``len(comments) == 1`` the prompt is identical to the
        legacy single-comment prompt (``_build_review_prompt``) so
        existing single-comment paths regress nothing. For 2+ the
        builder enumerates each comment with its file/line
        localization and asks the agent to address them in one
        coherent change-set.

        ``additional_dirs`` widens the sandbox (``--add-dir``) beyond
        ``cwd`` to every OTHER repo the task touches. Without this a
        multi-repo task's review-fix session was permanently scoped to
        just the repo the triggering comment happens to live on, even
        when the initial task-implementation session for the same task
        (``implement_task`` / ``_working_directories``) had access to
        every attached repo.
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
        if len(comments) == 1:
            single = comments[0]
            prompt = self._build_review_prompt(
                single, branch_name, workspace_path=cwd, mode=mode,
                workspace_refusal_guidance=self._workspace_refusal_guidance,
                self_reply_prefixes=self._self_reply_prefixes,
                additional_dirs=extra_dirs,
            )
        else:
            prompt = self._build_review_comments_batch_prompt(
                comments, branch_name, workspace_path=cwd, mode=mode,
                workspace_refusal_guidance=self._workspace_refusal_guidance,
                self_reply_prefixes=self._self_reply_prefixes,
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

    # ----- prompt builders (Claude-specific, share core helpers with the agent client) -----





    @staticmethod
    def _tool_guardrails_text() -> str:
        return (
            'Tool guardrails:\n'
            '- Use Edit/Write/Read for file edits and reads.\n'
            '- Use Bash sparingly and only for non-destructive shell needs (rg, sed -n, cat, ls).\n'
            '\n'
            'YOUR JOB IS TO EDIT FILES. THAT IS ALL.\n'
            '\n'
            'You do NOT do any of the following — ever, under any circumstance:\n'
            '- git (status, diff, log, add, commit, push, pull, fetch, checkout, switch, branch, reset, rebase, stash, tag, anything)\n'
            '- create pull requests / merge requests\n'
            '- call GitHub / GitLab / Bitbucket APIs\n'
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

    # ----- subprocess execution -----


    def _run_prompt(
        self,
        *,
        prompt: str,
        cwd: str,
        additional_dirs: list[str],
        agent_session_id: str = '',
        log_label: str = '',
        task_id: str = '',
    ) -> dict[str, str | bool]:
        command = self._build_command(
            additional_dirs=additional_dirs,
            agent_session_id=agent_session_id,
            cwd=cwd,
            resolve_binary=not self._docker_mode_on,
        )
        env = self._build_subprocess_env()
        log_label = log_label or 'Claude CLI'
        # Docker mode wraps the spawn in the hardened sandbox — see
        # ``the orchestrator.sandbox.manager``. Mirrors the streaming-session path
        # in ``StreamingClaudeSession.start`` via the shared
        # ``wrap_spawn_for_docker`` helper so test_task and investigate
        # get the same containment as the interactive planning sessions.
        # Gated on ``_docker_mode_on``, not ``_bypass_permissions``:
        # docker is containment, bypass is the prompt layer.
        spawn_cwd: str | None = cwd or None
        container_name = ''
        if self._docker_mode_on:
            workspace_path = cwd or self._repository_root_path or os.getcwd()
            command, container_name = wrap_spawn_for_docker(
                command,
                workspace_path=workspace_path,
                task_id=task_id or 'unknown',
                logger=self.logger,
            )
            # Docker sets the container WORKDIR to /workspace; the host
            # cwd is irrelevant for the docker client itself.
            spawn_cwd = None
        self.logger.info('Mission %s: invoking Claude CLI', log_label)
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                cwd=spawn_cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                check=False,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            if container_name:
                # subprocess.run's own TimeoutExpired handling SIGKILLs the
                # wrapping `docker run` client internally before raising —
                # SIGKILL can never be forwarded to the container, so
                # without this the container leaks and runs forever
                # (`--rm` only fires on the container's own clean exit).
                from sandbox_core_lib.sandbox_core_lib.manager import kill_container
                kill_container(container_name, logger=self.logger)
            raise TimeoutError(
                f'Claude CLI did not finish within {self._timeout_seconds}s for {log_label}'
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f'failed to invoke Claude CLI binary "{self._binary}": {exc}'
            ) from exc

        return self._parse_completed_process(completed, log_label=log_label)

    def _build_command(
        self,
        *,
        additional_dirs: list[str],
        agent_session_id: str,
        cwd: str = '',
        resolve_binary: bool = True,
        include_system_prompt: bool = True,
    ) -> list[str]:
        command: list[str] = [
            *(self._host_binary_argv() if resolve_binary else [self._binary]),
            '-p',
            '--output-format',
            'json',
            '--permission-mode',
            self._permission_mode,
        ]
        # Force out-of-workspace file writes (e.g. /tmp scratch) back through
        # the permission path — acceptEdits otherwise auto-accepts them with no
        # approval. Shared with the streaming builder; see write_scope_settings.
        from claude_core_lib.claude_core_lib.helpers.write_scope_settings import (
            out_of_workspace_write_settings_json,
        )
        command.extend(['--settings', out_of_workspace_write_settings_json(
            cwd, additional_dirs,
        )])
        append_model_effort_flags(
            command,
            model=self._model,
            max_turns=self._max_turns,
            effort=self._effort,
        )
        merged_allowed = self._merge_allowed_with_read_only_allowlist(self._allowed_tools)
        if merged_allowed:
            command.extend(['--allowedTools', merged_allowed])
        merged_disallowed = self._merge_disallowed_with_floor(
            self._disallowed_tools,
            bypass_permissions=self._bypass_permissions,
        )
        command.extend(['--disallowedTools', merged_disallowed])
        # ``--resume`` and ``--add-dir`` come BEFORE the system prompt:
        # it is the one multiline, unbounded-length argv value, and a
        # degraded batch-shim spawn (``_host_binary_argv`` fallback)
        # truncates the command line at its first newline — losing the
        # session pin produced the Windows resume-amnesia bug.
        normalized_session_id = fix_session_id(agent_session_id)
        if normalized_session_id:
            command.extend(['--resume', normalized_session_id])
        append_additional_dirs(command, additional_dirs)
        # ``include_system_prompt=False`` is for boot smoke-tests that
        # only need to confirm model reachability ("Reply with: ok").
        # Inlining the architecture doc + lessons there can push the
        # command line past Windows' CreateProcess limit (~32K chars,
        # less when the operator's PATH or env is unusual), surfacing
        # as ``[WinError 206] The filename or extension is too long``.
        # Real spawns still get the full system prompt — only the
        # validator skips it.
        if include_system_prompt:
            appended_system_prompt = build_appended_system_prompt(
                architecture_doc_path=self._architecture_doc_path,
                lessons_path=self._lessons_path,
                docker_mode_on=self._docker_mode_on,
                logger=self.logger,
            )
            if appended_system_prompt:
                command.extend(['--append-system-prompt', appended_system_prompt])
        command.extend(self._extra_args)
        return command

    def _merge_allowed_with_read_only_allowlist(self, operator_allowed: str) -> str:
        """Append the hardcoded read-only allowlist when the flag is on.

        When ``the read-only-tools setting=true`` (and docker
        is on — the startup gate refuses the flag without docker),
        every spawn pre-approves the entries in
        ``READ_ONLY_TOOLS_ALLOWLIST`` so the operator is not prompted
        for grep / rg / ls / cat / find / head / tail / wc / file /
        stat / Read.

        Operator extensions via ``the allowed-tools setting`` are
        preserved; the read-only allowlist is unioned in (no
        duplicates). When the flag is off, returns the operator
        value unchanged.

        The allowlist is hardcoded — the operator cannot widen it
        via env var. Adding a tool here is a security decision
        (an operator who picks the wrong "read-only" command silently
        widens the agent's blast radius); code-level edits force a
        review. The allowlist's exact membership is locked by a
        drift-guard test.
        """
        if not self._read_only_tools_on:
            return operator_allowed
        from sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator import (
            READ_ONLY_TOOLS_ALLOWLIST,
        )
        existing = [
            entry.strip()
            for entry in (operator_allowed or '').split(',')
            if entry.strip()
        ]
        seen = {entry: True for entry in existing}
        # Deterministic order so the resulting argv is stable across
        # runs (helps when comparing logs / audit entries).
        for pattern in sorted(READ_ONLY_TOOLS_ALLOWLIST):
            if pattern not in seen:
                existing.append(pattern)
                seen[pattern] = True
        return ','.join(existing)

    @staticmethod
    def _union_disallowed(operator_disallowed: str, patterns) -> str:
        """Union ``patterns`` into a CSV disallowed-tools string without
        duplicating entries and preserving the operator's order first."""
        existing = [
            entry.strip()
            for entry in (operator_disallowed or '').split(',')
            if entry.strip()
        ]
        seen = {entry: True for entry in existing}
        for pattern in patterns:
            if pattern not in seen:
                existing.append(pattern)
                seen[pattern] = True
        return ','.join(existing)

    @classmethod
    def _merge_disallowed_with_git_deny(cls, operator_disallowed: str) -> str:
        """Always include the git denylist, regardless of operator config.

        The operator can extend the denylist via ``the disallowed-tools setting``
        but cannot remove the git patterns. the orchestrator is the sole component that
        runs git operations.
        """
        return cls._union_disallowed(operator_disallowed, cls.GIT_DENY_PATTERNS)

    @classmethod
    def _merge_disallowed_with_floor(
        cls, operator_disallowed: str, *, bypass_permissions: bool = False,
    ) -> str:
        """Apply BOTH non-overridable floors — git mutations and the Action
        Guard no-legit-use programs — to the operator's disallowed list.

        This is the single floor every spawn ships (one-shot AND streaming),
        so the CLI refuses these tools in every permission mode.

        ``bypass_permissions`` additionally re-denies ``git restore``. That
        looks inconsistent with allowing it everywhere else, and it is the
        point: the whole-tree form is only safe to permit because Layer B
        routes it to the operator, and in bypassPermissions there is no
        per-tool prompt for Layer B to route to. Rather than let an
        unrecoverable ``git restore .`` run unattended, the capability
        reverts to its previous state in exactly the mode that cannot
        supervise it. Scoped reverts stay available in every attended mode,
        which is where the operator asked for them.
        """
        merged = cls._merge_disallowed_with_git_deny(operator_disallowed)
        merged = cls._union_disallowed(merged, cls.ACTION_GUARD_DENY_PATTERNS)
        if bypass_permissions:
            merged = cls._union_disallowed(merged, cls.UNSUPERVISED_DENY_PATTERNS)
        return merged

    def _build_subprocess_env(self) -> dict[str, str]:
        # Force JSON output to stdout and prevent any TTY-dependent
        # behavior. Shared invariant with the streaming path — see
        # ``build_claude_subprocess_env``.
        return build_claude_subprocess_env()

    def _parse_completed_process(
        self,
        completed: subprocess.CompletedProcess,
        *,
        log_label: str,
    ) -> dict[str, str | bool]:
        stdout = completed.stdout or ''
        stderr = (completed.stderr or '').strip()

        payload = self._parse_json_payload(stdout)

        is_error = bool(payload.get('is_error', False))
        success = completed.returncode == 0 and not is_error
        result_text = normalized_text(payload.get('result', ''))
        # ``payload`` is Claude CLI's terminal ``result`` event (wire
        # format) — Claude emits ``session_id``, the orchestrator normalizes
        # to ``AGENT_SESSION_ID`` downstream.
        session_id_value = fix_session_id(payload.get('session_id', ''))

        if completed.returncode != 0:
            detail = stderr or condensed_text(stdout) or 'no output'
            self.logger.error(
                'Claude CLI returned exit code %s for %s: %s',
                completed.returncode,
                log_label,
                detail,
            )
            raise RuntimeError(
                f'Claude CLI exited with status {completed.returncode}: {detail}'
            )
        if is_error:
            detail = result_text or stderr or 'unknown Claude CLI error'
            raise RuntimeError(f'Claude CLI reported an error: {detail}')

        # Output-side credential scan — closes residual #18 on the
        # detective side. The agent's response has already crossed to
        # Anthropic by the time we see it, so this cannot UNDO the
        # leak; it produces an auditable record so the operator knows
        # to rotate. Pattern names + redacted previews only — full
        # credential values are never logged.
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

    def _parse_json_payload(self, stdout: str) -> dict[str, object]:
        text = (stdout or '').strip()
        if not text:
            return {}

        # The CLI normally emits a single JSON object on stdout when called with
        # --output-format json. Fall back to scanning for the first balanced
        # JSON object so transient stdout chatter does not break parsing.
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = self._extract_first_json_object(text)
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    return item
        self.logger.warning(
            'failed to parse Claude CLI JSON output; got: %s',
            condensed_text(text)[:500],
        )
        return {}

    @staticmethod
    def _extract_first_json_object(text: str) -> object:
        brace_start = text.find('{')
        brace_end = text.rfind('}')
        if brace_start == -1 or brace_end <= brace_start:
            return {}
        try:
            return json.loads(text[brace_start:brace_end + 1])
        except json.JSONDecodeError:
            return {}

    # ----- working directory resolution -----

    # ----- smoke test -----

    def _run_model_access_validation(self) -> None:
        self.logger.info('running Claude CLI model access validation')
        # Smoke test sends ``Reply with exactly: ok`` — no need for the
        # architecture doc / lessons here. Skipping them keeps the
        # boot command line short, which matters on Windows where
        # CreateProcess caps total args at ~32K chars.
        command = self._build_command(
            additional_dirs=[], agent_session_id='',
            include_system_prompt=False,
        )
        env = self._build_subprocess_env()
        # Boot-time validator: fixed ``SMOKE_TEST_PROMPT`` ("Reply with
        # exactly: ok"), no tools, no untrusted input. Sandbox-wrap is
        # intentionally skipped even when ``the docker setting=true`` —
        # there is no workspace to leak from, the only egress is the
        # api.anthropic.com call that has to happen, and the operator
        # would pay container-spin cost on every startup with zero
        # security benefit.
        try:
            completed = subprocess.run(
                command,
                input=self.SMOKE_TEST_PROMPT,
                cwd=self._repository_root_path or None,
                env=env,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                check=False,
                timeout=self.SMOKE_TEST_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f'Claude CLI smoke test did not finish within {self.SMOKE_TEST_TIMEOUT_SECONDS}s'
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or '').strip() or 'unknown error'
            raise RuntimeError(f'Claude CLI smoke test failed: {detail}')
        payload = self._parse_json_payload(completed.stdout or '')
        if payload.get('is_error'):
            detail = text_from_mapping(payload, 'result') or 'unknown Claude CLI error'
            raise RuntimeError(f'Claude CLI smoke test reported an error: {detail}')

    # ----- helpers -----

