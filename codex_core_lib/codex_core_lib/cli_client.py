"""Drive OpenAI's Codex CLI (``codex exec``) as an orchestrator agent backend.

Same public surface as ``ClaudeCliClient`` so the orchestration
layer can use either backend interchangeably — selection is driven
by ``the agent-backend setting``. The Codex CLI is shaped quite differently
from Claude Code under the hood (sandbox modes instead of an
allow/deny tool list, a ``resume <id>`` subcommand instead of a
``--resume`` flag, JSONL events instead of one JSON object), but
the methods the orchestrator calls + the result shape the orchestrator receives are
identical.

This is a one-shot implementation (no streaming-session machinery
yet). Codex's interactive / streaming surface can be layered on
later under the same ``session/`` folder name claude uses, keeping
the structural parity the operator asked for.

Verified against ``codex-cli 0.132.0`` (the OpenAI ``@openai/codex``
package). When the CLI version moves, re-read ``codex exec --help``
and adjust the flag table below if it has changed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from agent_core_lib.agent_core_lib.cli_agent_shared import CliAgentSharedBehaviour
from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from agent_core_lib.agent_core_lib.helpers import agent_prompt_utils
from agent_core_lib.agent_core_lib.helpers.command_floor import (
    prompt_floor_rules,
)
from agent_core_lib.agent_core_lib.helpers.architecture_doc_utils import read_architecture_doc
from agent_core_lib.agent_core_lib.helpers.lessons_doc_utils import read_lessons_file
from agent_core_lib.agent_core_lib.helpers.logging_utils import configure_logger
from utils_core_lib.utils_core_lib.text_utils import (
    condensed_text,
    normalized_text,
)
from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from sandbox_core_lib.sandbox_core_lib.workspace_delimiter import (
    wrap_untrusted_workspace_content,
)


class CodexCliClient(CliAgentSharedBehaviour):
    """Drive OpenAI's Codex CLI as the implementation/testing backend.

    The public methods (``validate_connection``, ``implement_task``,
    ``test_task``, ``investigate``, ``fix_review_comment``,
    ``fix_review_comments``, ``delete_conversation``,
    ``stop_all_conversations``, ``validate_model_access``) match
    ``ClaudeCliClient`` one-to-one so callers do not branch on
    backend. The differences live inside ``_build_command`` (Codex
    CLI flag set) and ``_parse_completed_process`` (JSONL event
    stream + ``--output-last-message`` file).

    Constructor params that are Claude-specific (``max_turns``,
    ``effort``, ``allowed_tools``, ``disallowed_tools``,
    ``read_only_tools_on``) are accepted for API parity with
    ``ClaudeCliClient`` but are **no-ops** in this client — Codex
    has no equivalent flags. We log a single info message at
    construction so operators don't silently expect those knobs to
    bite.
    """

    DEFAULT_BINARY = 'codex'
    CLI_DISPLAY_NAME = 'Codex'
    DEFAULT_TIMEOUT_SECONDS = 1800
    # Codex 0.132 has NO ``--ask-for-approval`` flag on ``codex exec``
    # — that flag is only on the top-level interactive ``codex``
    # command. For the non-interactive ``codex exec`` path the
    # approval policy comes from ``~/.codex/config.toml`` (key
    # ``approval_policy``) or from a ``-c approval_policy=<value>``
    # override. We don't set one by default and trust the operator's
    # codex config; the real safety boundary in safe mode is the
    # ``--sandbox`` flag below.
    #
    # Codex's sandbox policy values (--sandbox). ``workspace-write``
    # lets the agent edit files inside the workspace directory tree
    # but blocks writes elsewhere — the right default for the orchestrator's
    # per-task clone model. ``danger-full-access`` removes the
    # filesystem boundary; we never set that automatically.
    SAFE_SANDBOX_MODE = 'workspace-write'
    SMOKE_TEST_PROMPT = 'Reply with exactly: ok. Do not call any tools.'
    SMOKE_TEST_TIMEOUT_SECONDS = 120
    VERSION_PROBE_TIMEOUT_SECONDS = 30

    # Effort param is accepted for parity with ClaudeCliClient but
    # not translated to a flag — Codex routes reasoning depth via
    # the ``model_reasoning_effort`` config key in
    # ``~/.codex/config.toml``, not a per-invocation flag. Operators
    # who care can set it there.
    SUPPORTED_EFFORT_LEVELS = frozenset({'low', 'medium', 'high', 'xhigh', 'max'})

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
        # Stored for API parity but not emitted as a flag — Codex
        # has no per-invocation turn cap.
        self._max_turns = self._coerce_max_turns(max_turns)
        # Same: accepted for parity, validated, but not used. Effort
        # routing is via ``-c model_reasoning_effort=...`` only when
        # we're confident the operator's codex install has that key.
        self._effort = self._coerce_effort(effort)
        self._bypass_permissions = bool(bypass_permissions)
        self._docker_mode_on = bool(docker_mode_on)
        self._read_only_tools_on = bool(read_only_tools_on)
        # Stored for API parity. Codex uses sandbox modes + execpolicy
        # ``.rules`` files instead of a per-spawn allow/deny tool
        # list, so these are not translated to flags here.
        self._allowed_tools = normalized_text(allowed_tools)
        self._disallowed_tools = normalized_text(disallowed_tools)
        self._timeout_seconds = max(60, int(timeout_seconds or self.DEFAULT_TIMEOUT_SECONDS))
        self._repository_root_path = normalized_text(repository_root_path)
        self._model_smoke_test_enabled = bool(model_smoke_test_enabled)
        self._model_access_smoke_test_ran = False
        self._extra_args = list(extra_args or [])
        self._architecture_doc_path = normalized_text(architecture_doc_path)
        self._lessons_path = normalized_text(lessons_path)
        # Product-specific refusal guidance appended to the generic
        # workspace scope block; supplied by the spawner ('' otherwise).
        self._workspace_refusal_guidance = workspace_refusal_guidance or ''
        # Host bot's review-reply prefixes — drop the bot's own prior replies
        # from review-comment context. Empty = no filtering (agnostic default).
        self._self_reply_prefixes = tuple(self_reply_prefixes or ())
        self.logger = configure_logger(self.__class__.__name__)
        if self._bypass_permissions:
            self.logger.warning(
                'Bypass-permissions mode is enabled: Codex will run with '
                '--dangerously-bypass-approvals-and-sandbox. Per-tool '
                'prompts and sandbox containment are BOTH disabled — the '
                'agent can run shell, edit, write, and any other tool '
                'against any path it can reach. The operator who enabled '
                'this accepts responsibility for any harm caused by the '
                'agent. See SECURITY.md.'
            )
        if (
            self._allowed_tools or self._disallowed_tools
            or self._max_turns is not None or self._effort
            or self._read_only_tools_on
        ):
            # One line so operators using the same .env across both
            # backends know which knobs the codex backend silently
            # ignores instead of debugging mid-run.
            self.logger.info(
                'Codex backend ignores allowed_tools / disallowed_tools / '
                'max_turns / effort / read_only_tools_on — those are '
                'Claude Code concepts with no Codex CLI 0.132.x equivalent. '
                'Use --sandbox mode (auto-set by the host) and '
                '~/.codex/config.toml for similar controls.'
            )

    # ----- public agent-client API (parity with the other transports) -----

    def validate_connection(self) -> None:
        if self._running_inside_docker():
            raise RuntimeError(
                'The Codex backend is not supported inside Docker. '
                'The Codex CLI authenticates against your host credentials '
                '(``codex login`` writes to ``$CODEX_HOME`` on the host), '
                'and the container cannot reach those. Run the agent on the '
                'host instead, or select a backend that supports '
                'containerized execution (e.g. OpenHands).'
            )
        binary_path = shutil.which(self._binary)
        if not binary_path:
            raise RuntimeError(
                f'\n'
                f'Codex CLI ("{self._binary}") was not found on PATH.\n'
                f'\n'
                f'Install Codex CLI (works on macOS, Linux, and Windows):\n'
                f'\n'
                f'    npm install -g @openai/codex\n'
                f'\n'
                f'Prerequisite: Node.js 18+ (https://nodejs.org/). Verify with:\n'
                f'\n'
                f'    node --version\n'
                f'    codex --version\n'
                f'\n'
                f'After install, the ``codex`` binary must be on PATH (npm puts it\n'
                f'there automatically). If you installed it somewhere else,\n'
                f'configure the Codex binary path. Authenticate once with\n'
                f'``codex login``.\n'
            )
        self._binary_path = binary_path
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
                f'Codex CLI binary "{self._binary}" failed to launch: {exc}'
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip() or 'unknown error'
            raise RuntimeError(
                f'Codex CLI binary "{self._binary}" failed to report a version: {detail}'
            )
        version_text = condensed_text(result.stdout)
        self.logger.info(
            'Codex CLI is available at %s (%s)',
            binary_path,
            version_text,
        )
        self._validate_streaming_support(binary_path, version_text)
        self._validate_model_smoke_test()

    def _validate_streaming_support(self, binary_path: str, version_text: str) -> None:
        """Refuse a CLI too old to stream, BEFORE a turn depends on it.

        Every chat turn runs ``codex exec --json`` and parses the JSONL it
        emits. The pre-Rust Codex CLI has no such flag, so the turn died on
        ``error: unknown option '--json'`` — mid-conversation, after the
        operator had typed a message, with nothing saying which binary was
        at fault or what to do about it.

        Feature-detected rather than version-compared: the flag is the thing
        actually depended on, and a version table would need editing every
        time the CLI is repackaged. A help probe that cannot run is NOT
        treated as a failure — a sandbox that blocks it must not make an
        otherwise-working CLI look broken.
        """
        try:
            probe = subprocess.run(
                [*self._host_binary_argv(), 'exec', '--help'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                check=False,
                timeout=self.VERSION_PROBE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            return
        if probe.returncode != 0:
            return
        if '--json' in (probe.stdout or '') + (probe.stderr or ''):
            return
        raise RuntimeError(
            f'\n'
            f'The Codex CLI at {binary_path} is too old for streaming chat.\n'
            f'\n'
            f'    reported version: {version_text or "unknown"}\n'
            f'\n'
            f'Each turn is streamed with `codex exec --json`, and this build\n'
            f'does not support that flag — a chat would fail mid-turn with\n'
            f'"unknown option \'--json\'". Upgrade it:\n'
            f'\n'
            f'    npm install -g @openai/codex@latest\n'
            f'\n'
            f'Then check with `codex --version`. If several copies are\n'
            f'installed, make sure the one on PATH is the upgraded one — or\n'
            f'set the Codex binary path in Settings to point at it.\n'
        )

    def _wrap_untrusted(self, text: str, *, source_path: str) -> str:
        """Bind the shared prompt scaffolding to the delimiter framing."""
        return wrap_untrusted_workspace_content(text, source_path=source_path)

    @classmethod
    def _wrap_untrusted_text(cls, text: str, *, source_path: str) -> str:
        """Bind the shared prompt scaffolding to the delimiter framing."""
        return wrap_untrusted_workspace_content(text, source_path=source_path)

    @classmethod
    def _read_only_instruction(cls) -> str:
        """Codex exposes generic tooling, so the rule names the MODE."""
        return '- Do NOT modify any files. Stay in read-only mode.\n'

    def investigate(self, prompt: str, *, cwd: str = '') -> str:
        """Read-only single turn — used by the triage flow.

        Codex's ``--sandbox read-only`` policy enforces the read-only
        contract at the sandbox layer, which is stronger than
        Claude's allow/deny tool list.
        """
        normalized_prompt = normalized_text(prompt)
        if not normalized_prompt:
            raise ValueError('prompt is required to run an investigation')
        normalized_cwd = normalized_text(cwd)
        if not normalized_cwd:
            normalized_cwd = self._repository_root_path or os.getcwd()
        # Temporarily flip the sandbox to read-only so we cannot
        # accidentally mutate the workspace during triage. Restore
        # whatever was in place on the way out.
        original_bypass = self._bypass_permissions
        try:
            self._bypass_permissions = False  # never bypass on triage
            payload = self._run_prompt(
                prompt=normalized_prompt,
                cwd=normalized_cwd,
                additional_dirs=[],
                log_label='triage investigation',
                task_id='triage',
                sandbox_override='read-only',
            )
        finally:
            self._bypass_permissions = original_bypass
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
        """Address one or more PR review comments in a single spawn.

        Mirrors ``ClaudeCliClient.fix_review_comments`` — same signature,
        same return contract, same ``mode`` semantics (``fix`` vs
        ``answer``), same ``additional_dirs`` sandbox-widening semantics.
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
        # Codex has no ``--append-system-prompt`` flag (unlike Claude, where
        # this is attached to the CLI invocation itself and so applies to
        # EVERY spawn uniformly) — every Codex prompt builder must splice it
        # into the prompt text individually. The implementation/testing
        # builders already did; the review builders did not, which meant a
        # review-fix/-answer spawn got no architecture doc, no lessons, and
        # (once the untrusted-workspace-content explanation became always-on
        # rather than docker-gated) no explanation of what the
        # UNTRUSTED_WORKSPACE_FILE tags around comment.body even mean.
        system_addendum = self._system_prompt_addendum()
        if len(comments) == 1:
            single = comments[0]
            prompt = self._build_review_prompt(
                single, branch_name, workspace_path=cwd, mode=mode,
                workspace_refusal_guidance=self._workspace_refusal_guidance,
                self_reply_prefixes=self._self_reply_prefixes,
                system_addendum=system_addendum,
                additional_dirs=extra_dirs,
            )
        else:
            prompt = self._build_review_comments_batch_prompt(
                comments, branch_name, workspace_path=cwd, mode=mode,
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

    # ----- prompt builders -----





    def _system_prompt_addendum(self) -> str:
        """Codex has no ``--append-system-prompt`` flag, so the
        architecture-doc + lessons text is prepended to the user
        prompt instead. Same payload Claude gets through
        ``--append-system-prompt``."""
        architecture_doc = read_architecture_doc(
            self._architecture_doc_path, logger=self.logger,
        )
        lessons_text = read_lessons_file(
            self._lessons_path, logger=self.logger,
        )
        from sandbox_core_lib.sandbox_core_lib.system_prompt import compose_system_prompt
        return compose_system_prompt(
            architecture_doc,
            docker_mode_on=self._docker_mode_on,
            lessons=lessons_text,
        )

    @staticmethod
    def _tool_guardrails_text() -> str:
        # Codex has no allow/deny tool list flag, so the prompt-level
        # rules are the only layer that says "no git". The
        # ``workspace-write`` sandbox limits writes to the workspace
        # dir, but does NOT block ``git`` shell calls inside it. This
        # block is therefore load-bearing for the codex backend in a
        # way it is only defense-in-depth for claude.
        return (
            'Tool guardrails:\n'
            '- Use edit/write/read tooling for file edits and reads.\n'
            '- Use the shell sparingly and only for non-destructive needs (rg, sed -n, cat, ls).\n'
            '\n'
            # Rendered from the SAME floor the deny-flag transports enforce, so
            # this prompt cannot quietly name a different set. Prompt text is a
            # weaker layer than an unavailable tool — that is exactly why it
            # must not also be a hand-maintained second list.
            f'{prompt_floor_rules()}'
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
        sandbox_override: str = '',
    ) -> dict[str, str | bool]:
        # ``--output-last-message <file>`` is the cleanest way to get
        # the agent's final reply text from a non-interactive run —
        # the alternative is parsing the JSONL event stream for the
        # last ``agent_message`` event, whose exact event-name we'd
        # be guessing at across codex versions.
        fd, last_message_file = tempfile.mkstemp(prefix='codex-last-', suffix='.txt')
        os.close(fd)
        try:
            command = self._build_command(
                additional_dirs=additional_dirs,
                agent_session_id=agent_session_id,
                resolve_binary=not self._docker_mode_on,
                last_message_file=last_message_file,
                cwd=cwd,
                sandbox_override=sandbox_override,
            )
            env = self._build_subprocess_env()
            log_label = log_label or 'Codex CLI'
            spawn_cwd: str | None = cwd or None
            container_name = ''
            if self._docker_mode_on:
                from sandbox_core_lib.sandbox_core_lib.manager import (
                    SandboxError,
                    arm_container_watchdog,
                    check_spawn_rate,
                    ensure_image,
                    enforce_no_workspace_secrets,
                    make_container_name,
                    record_spawn,
                    start_task_egress_proxy,
                    wrap_command,
                )
                workspace_path = cwd or self._repository_root_path or os.getcwd()
                try:
                    ensure_image(logger=self.logger)
                except SandboxError as exc:
                    raise RuntimeError(
                        f'failed to prepare Codex sandbox image: {exc}',
                    ) from exc
                try:
                    check_spawn_rate()
                except SandboxError as exc:
                    raise RuntimeError(
                        f'sandbox spawn rate-limited: {exc}',
                    ) from exc
                container_name = make_container_name(task_id)
                try:
                    enforce_no_workspace_secrets(workspace_path, logger=self.logger)
                except SandboxError as exc:
                    raise RuntimeError(
                        f'sandbox spawn blocked: {exc}',
                    ) from exc
                # Per-task egress proxy, exactly as the other CLI transport
                # gets it. Without these two calls this path silently ran
                # with the older, weaker egress rule (shared bridge +
                # address allowlist) and no parent-loss watchdog — a
                # difference nothing in the spawn output would show.
                try:
                    task_network, proxy_ip = start_task_egress_proxy(
                        container_name, logger=self.logger,
                    )
                except SandboxError as exc:
                    raise RuntimeError(
                        f'sandbox egress setup failed: {exc}',
                    ) from exc
                command = wrap_command(
                    command,
                    workspace_path=workspace_path,
                    container_name=container_name,
                    task_id=task_id or 'unknown',
                    task_network=task_network,
                    proxy_ip=proxy_ip,
                )
                try:
                    record_spawn(
                        task_id=task_id or 'unknown',
                        container_name=container_name,
                        workspace_path=workspace_path,
                        logger=self.logger,
                    )
                except SandboxError as exc:
                    raise RuntimeError(
                        f'sandbox audit log required but failed: {exc}',
                    ) from exc
                arm_container_watchdog(
                    container_name, workspace_path=workspace_path,
                    logger=self.logger,
                )
                spawn_cwd = None
            self.logger.info('Mission %s: invoking Codex CLI', log_label)
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
                    # subprocess.run's own TimeoutExpired handling SIGKILLs
                    # the wrapping `docker run` client internally before
                    # raising — SIGKILL can never be forwarded to the
                    # container, so without this the container leaks and
                    # runs forever (`--rm` only fires on the container's
                    # own clean exit).
                    from sandbox_core_lib.sandbox_core_lib.manager import kill_container
                    kill_container(container_name, logger=self.logger)
                raise TimeoutError(
                    f'Codex CLI did not finish within {self._timeout_seconds}s for {log_label}'
                ) from exc
            except OSError as exc:
                raise RuntimeError(
                    f'failed to invoke Codex CLI binary "{self._binary}": {exc}'
                ) from exc

            return self._parse_completed_process(
                completed,
                log_label=log_label,
                last_message_file=last_message_file,
            )
        finally:
            try:
                os.unlink(last_message_file)
            except OSError:
                pass

    def _build_command(
        self,
        *,
        additional_dirs: list[str],
        agent_session_id: str,
        resolve_binary: bool = True,
        include_system_prompt: bool = True,  # accepted for API parity, see note
        last_message_file: str = '',
        cwd: str = '',
        sandbox_override: str = '',
    ) -> list[str]:
        """Build the argv for one ``codex exec`` (or ``codex exec resume``) spawn.

        Verified against ``codex-cli 0.132.0``.

        Important shape differences from Claude:

        * Resume is a sub-subcommand (``codex exec resume <id>``), not
          a flag. The resume subcommand has a **restricted option set**
          — ``--sandbox`` / ``-C`` / ``--add-dir`` are NOT accepted
          (the resumed session inherits those settings from its
          original spawn). Only ``--json``, ``-o``, ``-m``,
          ``--skip-git-repo-check``, ``--dangerously-bypass-*`` and
          ``-c`` overrides pass through.
        * ``--ask-for-approval`` is **not** on ``codex exec`` at all —
          it's a top-level interactive-mode option. Approval policy
          for non-interactive runs comes from ``~/.codex/config.toml``
          (or ``-c approval_policy=<value>`` override). the orchestrator leaves
          it to the operator's config and relies on ``--sandbox`` as
          the real safety boundary.
        * The ``include_system_prompt`` param is accepted for parity
          with ``ClaudeCliClient._build_command`` but is **not used**
          here — Codex 0.132 has no ``--append-system-prompt``
          equivalent, so the system-prompt addendum is woven into
          the prompt body in the implement / test prompt builders.
        """
        del include_system_prompt  # see docstring
        command: list[str] = [
            *(self._host_binary_argv() if resolve_binary else [self._binary]),
            'exec',
        ]
        normalized_session_id = normalized_text(agent_session_id)
        is_resume = bool(normalized_session_id)
        if is_resume:
            command.extend(['resume', normalized_session_id])

        # Flags accepted by BOTH ``codex exec`` and ``codex exec resume``.
        command.extend([
            '--json',                  # JSONL event stream on stdout
            '--skip-git-repo-check',   # workspace clones may not be a git root
        ])

        # Bypass is a single flag that works on both exec and resume
        # and overrides everything else. Conflicts with --sandbox so
        # don't emit that alongside it.
        if self._bypass_permissions:
            command.append('--dangerously-bypass-approvals-and-sandbox')
        elif not is_resume:
            # --sandbox is ONLY accepted on fresh ``codex exec``; the
            # resume subcommand inherits sandbox mode from the
            # session being resumed.
            sandbox = normalized_text(sandbox_override) or self.SAFE_SANDBOX_MODE
            command.extend(['--sandbox', sandbox])

        if self._model:
            command.extend(['-m', self._model])

        if not is_resume:
            # -C and --add-dir are ONLY accepted on fresh ``codex exec``;
            # resumed sessions inherit their working set.
            normalized_cwd = normalized_text(cwd)
            if normalized_cwd:
                command.extend(['-C', normalized_cwd])
            for directory in additional_dirs:
                normalized_dir = normalized_text(directory)
                if normalized_dir:
                    command.extend(['--add-dir', normalized_dir])

        if last_message_file:
            command.extend(['-o', last_message_file])

        command.extend(self._extra_args)
        return command

    def _build_subprocess_env(self) -> dict[str, str]:
        # Codex has no documented ``CODEX_NONINTERACTIVE`` env knob;
        # ``--json`` on the subcommand already forces non-interactive
        # behaviour. Inherit the operator's environment so ``$CODEX_HOME``,
        # ``$OPENAI_API_KEY``, and auth state continue to work.
        return os.environ.copy()

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

        # Prefer the file the CLI wrote (``--output-last-message``)
        # over whatever we managed to recover from JSONL — the file
        # contract is what codex documents; JSONL event names can
        # drift between versions.
        file_message = ''
        if last_message_file:
            try:
                with open(last_message_file, 'r', encoding='utf-8') as handle:
                    file_message = handle.read().strip()
            except OSError:
                file_message = ''

        is_error = bool(payload.get('is_error', False))
        success = completed.returncode == 0 and not is_error
        # For result text, prefer ``--output-last-message`` file (always
        # set on success). For error text, prefer the parsed JSONL error
        # (operator-readable like "model X not supported") over raw
        # stderr (often shell-snapshot noise from codex's own bookkeeping
        # — we observed this on every probe).
        parsed_error_text = normalized_text(payload.get('result', '')) if is_error else ''
        result_text = file_message or normalized_text(payload.get('result', ''))
        session_id_value = normalized_text(payload.get(ImplementationFields.AGENT_SESSION_ID, ''))

        if completed.returncode != 0:
            detail = parsed_error_text or stderr or condensed_text(stdout) or 'no output'
            self.logger.error(
                'Codex CLI returned exit code %s for %s: %s',
                completed.returncode,
                log_label,
                detail,
            )
            raise RuntimeError(
                f'Codex CLI exited with status {completed.returncode}: {detail}'
            )
        if is_error:
            detail = parsed_error_text or stderr or 'unknown Codex CLI error'
            raise RuntimeError(f'Codex CLI reported an error: {detail}')

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

    def _parse_jsonl_payload(self, stdout: str) -> dict[str, object]:
        """Parse the ``--json`` JSONL event stream into an orchestrator-shaped dict.

        Verified against a real ``codex exec --json`` run on
        codex-cli 0.132.0. Observed event shapes:

        * ``{"type": "thread.started", "thread_id": "<uuid>"}`` —
          this is where the session-id-equivalent comes from. Codex
          calls it ``thread_id``; the orchestrator's contract calls it
          ``agent_session_id``, so we translate. ``codex exec resume <id>``
          accepts the thread_id as its positional argument.
        * ``{"type": "turn.started"}`` — informational.
        * ``{"type": "item.completed", "item": {"type": "agent_message",
          "text": "..."}}`` — the agent's final reply, nested under
          ``item``. Primary source for result text is still the
          ``--output-last-message`` file (documented contract); this
          parser is the fallback.
        * ``{"type": "turn.completed", "usage": {...}}`` — token-usage
          stats at the end.

        Error events haven't been observed yet (the success-case probe
        emitted none). The error detection in :func:`_extract_error_text`
        is heuristic — anything whose type contains ``error`` /
        ``failed`` / ``fail`` flips ``is_error``. If future codex
        versions surface a concrete error event name, add it to
        :data:`_ERROR_EVENT_TYPES`.
        """
        payload: dict[str, object] = {
            ImplementationFields.AGENT_SESSION_ID: '',
            'is_error': False,
            'result': '',
        }
        if not stdout:
            return payload
        for raw_line in stdout.splitlines():
            event = _parse_json_event(raw_line)
            if event is None:
                continue
            _absorb_thread_id(event, payload)
            _absorb_agent_message(event, payload)
            _absorb_error(event, payload)
        return payload

    # ----- working directory resolution -----

    # ----- smoke test -----

    def _run_model_access_validation(self) -> None:
        self.logger.info('running Codex CLI model access validation')
        # Smoke test: short prompt, no tools needed, no
        # ``--output-last-message`` file (we only care that the spawn
        # exited 0 and the JSONL stream had no error events).
        command = self._build_command(
            additional_dirs=[], agent_session_id='',
            include_system_prompt=False,
        )
        env = self._build_subprocess_env()
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
                f'Codex CLI smoke test did not finish within {self.SMOKE_TEST_TIMEOUT_SECONDS}s'
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or '').strip() or 'unknown error'
            raise RuntimeError(f'Codex CLI smoke test failed: {detail}')
        payload = self._parse_jsonl_payload(completed.stdout or '')
        if payload.get('is_error'):
            detail = str(payload.get('result') or '') or 'unknown Codex CLI error'
            raise RuntimeError(f'Codex CLI smoke test reported an error: {detail}')

    # ----- helpers -----

_ERROR_EVENT_TYPES = frozenset({
    # Concrete failure-event type names observed in real codex
    # 0.132.0 runs. The substring heuristic in ``_absorb_error``
    # below additionally catches anything with ``error`` / ``fail``
    # in the type so a future renamed event still trips the flag.
    'error',         # top-level model/API error
    'turn.failed',   # turn-scope failure
})


def _parse_json_event(raw_line: str) -> dict | None:
    """Parse one JSONL line, tolerating noise / banners / blanks."""
    line = raw_line.strip()
    if not line or not line.startswith('{'):
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):  # pragma: no cover
        # Unreachable in practice: the caller's ``startswith('{')``
        # guard above means any successful ``json.loads`` here MUST
        # produce a dict (a JSON ``{...}`` literal is by spec an
        # object). Defensive belt-and-braces for future refactors.
        return None
    return event


def _absorb_thread_id(event: dict, payload: dict[str, object]) -> None:
    """Codex calls it ``thread_id`` (translated to ``AGENT_SESSION_ID`` for the orchestrator).

    Falls back to a top-level ``session_id`` key on any event for
    forward-compat with future codex versions. ``event`` is the raw
    codex CLI JSON envelope — its keys are codex's wire format, not
    the orchestrator's internal names — so the literal ``'session_id'`` is what
    codex would emit if it ever surfaced one. ``payload`` is the orchestrator's
    internal dict, so its key is ``AGENT_SESSION_ID``.
    """
    if payload.get(ImplementationFields.AGENT_SESSION_ID):
        return
    tid = event.get('thread_id') or event.get('session_id', '')
    if tid:
        payload[ImplementationFields.AGENT_SESSION_ID] = str(tid).strip()


def _absorb_agent_message(event: dict, payload: dict[str, object]) -> None:
    """Pull ``item.text`` out of an ``item.completed`` agent-message event."""
    if str(event.get('type', '')) != 'item.completed':
        return
    item = event.get('item') or {}
    if not isinstance(item, dict) or item.get('type') != 'agent_message':
        return
    text = item.get('text', '')
    if text:
        payload['result'] = str(text).strip()


def _absorb_error(event: dict, payload: dict[str, object]) -> None:
    """Heuristic error detection. See :meth:`CodexCliClient._parse_jsonl_payload`.

    Real codex 0.132 emits errors in two shapes:

    * ``{"type":"error","message":"<JSON-encoded backend envelope>"}``
      — the API error from OpenAI's backend, stringified into the
      ``message`` field. We unwrap one JSON level so operators see
      the inner ``error.message`` text instead of the wire envelope.
    * ``{"type":"turn.failed","error":{"message":"..."}}`` — error
      nested one level deep under ``error``. We dig in.
    """
    event_type = str(event.get('type', ''))
    is_error_event = (
        event_type in _ERROR_EVENT_TYPES
        or 'error' in event_type
        or 'fail' in event_type
    )
    if not is_error_event:
        return
    payload['is_error'] = True
    err_text = _extract_error_text(event)
    if err_text:
        payload['result'] = _unwrap_backend_error_envelope(err_text)


def _extract_error_text(event: dict) -> str:
    """Pull the error string out of whichever shape codex chose this turn."""
    top = event.get('message')
    if isinstance(top, str) and top:
        return top
    nested = event.get('error')
    if isinstance(nested, dict):
        for key in ('message', 'error'):
            val = nested.get(key)
            if isinstance(val, str) and val:
                return val
    elif isinstance(nested, str) and nested:
        return nested
    item = event.get('item')
    if isinstance(item, dict):
        for key in ('text', 'message'):
            val = item.get(key)
            if isinstance(val, str) and val:
                return val
    return ''


def _unwrap_backend_error_envelope(text: str) -> str:
    """Backend errors come JSON-stringified inside the JSONL message.

    Example real payload from codex 0.132:
        '{"type":"error","status":400,"error":{"type":"invalid_request_error",
          "message":"The X model is not supported..."}}'

    Operators want to see the inner ``error.message``, not the wire
    envelope. Unwrap one level if it parses; otherwise return as-is.
    """
    stripped = text.strip()
    if not stripped.startswith('{'):
        return stripped
    try:
        inner = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        return stripped
    if not isinstance(inner, dict):  # pragma: no cover
        # Unreachable in practice: the ``startswith('{')`` guard above
        # means any successful ``json.loads`` here MUST produce a dict
        # (a JSON ``{...}`` literal is by spec an object). Defensive
        # belt-and-braces for future refactors.
        return stripped
    return _readable_message_from_envelope(inner) or stripped


def _readable_message_from_envelope(envelope: dict) -> str:
    """Pull the operator-readable string out of a parsed backend envelope."""
    nested = envelope.get('error')
    if isinstance(nested, dict):
        for key in ('message', 'error'):
            val = nested.get(key)
            if isinstance(val, str) and val:
                return val.strip()
    top = envelope.get('message')
    if isinstance(top, str) and top:
        return top.strip()
    return ''




