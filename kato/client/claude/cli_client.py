from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from kato.data_layers.data.fields import ImplementationFields
from kato.data_layers.data.review_comment import ReviewComment
from kato.data_layers.data.task import Task
from kato.helpers import agent_prompt_utils
from kato.helpers.architecture_doc_utils import read_architecture_doc
from kato.helpers.kato_result_utils import build_openhands_result
from kato.helpers.logging_utils import configure_logger
from kato.helpers.task_context_utils import PreparedTaskContext
from kato.helpers.text_utils import (
    condensed_text,
    normalized_text,
    text_from_attr,
    text_from_mapping,
)


class ClaudeCliClient(object):
    """Drive Anthropic's Claude Code CLI (`claude -p`) as the implementation/testing backend.

    Provides the same public interface as :class:`KatoClient` so the rest of the
    orchestration layer can use either backend interchangeably. Selection is
    driven by the ``KATO_AGENT_BACKEND`` environment variable.
    """

    DEFAULT_BINARY = 'claude'
    DEFAULT_TIMEOUT_SECONDS = 1800
    SAFE_PERMISSION_MODE = 'acceptEdits'
    BYPASS_PERMISSION_MODE = 'bypassPermissions'
    DEFAULT_ALLOWED_TOOLS = 'Edit,Write,Read,Bash,Glob,Grep'
    # Hard, non-overridable denylist. Kato is the only component that
    # ever runs git operations (commit, push, branch, reset, fetch,
    # rebase, ...). Claude must NEVER invoke git directly: it would race
    # with kato's branch state machine, bypass the publish-step retry
    # logic, and could push work kato hasn't validated. Every shape of
    # `git ...` we know Claude Code's allow-pattern matcher recognizes
    # is listed here. The two patterns cover both the colon-form
    # (`Bash(git:*)`) and the bare-form (`Bash(git *)`) that Claude
    # versions accept.
    GIT_DENY_PATTERNS = ('Bash(git:*)', 'Bash(git *)')
    SMOKE_TEST_PROMPT = 'Reply with exactly: ok. Do not call any tools.'
    SMOKE_TEST_TIMEOUT_SECONDS = 120
    VERSION_PROBE_TIMEOUT_SECONDS = 30

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
        max_retries: int = 3,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        repository_root_path: str = '',
        model_smoke_test_enabled: bool = False,
        extra_args: list[str] | None = None,
        effort: str = '',
        architecture_doc_path: str = '',
    ) -> None:
        self.max_retries = max(1, int(max_retries or 1))
        self._binary = normalized_text(binary) or self.DEFAULT_BINARY
        self._model = normalized_text(model)
        self._max_turns = self._coerce_max_turns(max_turns)
        self._effort = self._coerce_effort(effort)
        self._bypass_permissions = bool(bypass_permissions)
        # When not bypassing, pre-approve a safe default tool list so the
        # agent does not stall asking for permission in headless `-p` mode.
        # Users can override or extend via KATO_CLAUDE_ALLOWED_TOOLS.
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
        self.logger = configure_logger(self.__class__.__name__)
        if self._bypass_permissions:
            self.logger.warning(
                'KATO_CLAUDE_BYPASS_PERMISSIONS=true: Claude will run with '
                '--permission-mode bypassPermissions. Per-tool prompts are '
                'disabled — the agent can run Bash, Edit, Write, and any '
                'other tool without asking. The operator who set this flag '
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

    # ----- public API parity with KatoClient -----

    @staticmethod
    def _running_inside_docker() -> bool:
        # /.dockerenv is the canonical marker the Docker engine creates
        # inside every container it starts. A few non-Docker runtimes (e.g.
        # Podman with --root, some CI sandboxes) also create it, which is
        # fine for our purposes — anything that quacks like a container
        # also can't reach the host's macOS Keychain or `claude login`.
        return Path('/.dockerenv').exists()

    def validate_connection(self) -> None:
        if self._running_inside_docker():
            raise RuntimeError(
                'KATO_AGENT_BACKEND=claude is not supported inside Docker. '
                'The Claude Code CLI authenticates against your host '
                '`claude login` credentials (macOS Keychain, Linux config '
                'file, or Windows Credential Manager), and the container '
                'cannot reach those. '
                'Run kato locally instead — `make compose-up` or `make run`. '
                'If you genuinely need Docker, switch to KATO_AGENT_BACKEND=openhands '
                'and use `make compose-up-docker`.'
            )
        binary_path = shutil.which(self._binary)
        if not binary_path:
            raise RuntimeError(
                f'Claude CLI binary "{self._binary}" was not found on PATH. '
                'Install Claude Code from https://docs.claude.com/en/docs/claude-code/setup '
                'and ensure the `claude` binary is on PATH, or set KATO_CLAUDE_BINARY.'
            )
        try:
            result = subprocess.run(
                [self._binary, '--version'],
                capture_output=True,
                text=True,
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

    def validate_model_access(self) -> None:
        self._validate_model_access_smoke_test()

    def delete_conversation(self, conversation_id: str) -> None:
        # Claude CLI sessions are stored locally on disk; nothing to clean up
        # remotely. The orchestration layer treats this as a best-effort cleanup
        # hook, so a no-op is correct.
        return

    def stop_all_conversations(self) -> None:
        # No remote agent-server containers exist for the Claude CLI backend.
        return

    def implement_task(
        self,
        task: Task,
        session_id: str = '',
        prepared_task: PreparedTaskContext | None = None,
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
            session_id=session_id,
            log_label=agent_prompt_utils.task_conversation_title(task),
        )
        self.logger.info(
            'implementation finished for task %s with success=%s',
            task.id,
            result[ImplementationFields.SUCCESS],
        )
        return result

    def test_task(
        self,
        task: Task,
        prepared_task: PreparedTaskContext | None = None,
    ) -> dict[str, str | bool]:
        self.logger.info('requesting testing validation for task %s', task.id)
        prompt = self._build_testing_prompt(task, prepared_task)
        cwd, additional_dirs = self._working_directories(prepared_task)
        result = self._run_prompt_result(
            prompt=prompt,
            cwd=cwd,
            additional_dirs=additional_dirs,
            log_label=agent_prompt_utils.task_conversation_title(task, suffix=' [testing]'),
        )
        self.logger.info(
            'testing validation finished for task %s with success=%s',
            task.id,
            result[ImplementationFields.SUCCESS],
        )
        return result

    def investigate(self, prompt: str, *, cwd: str = '') -> str:
        """Run a single read-only Claude turn and return the raw text.

        Used by the triage flow: kato hands Claude a task description
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
            self._disallowed_tools = 'Edit,Write,MultiEdit,NotebookEdit,Bash,WebFetch'
            self._allowed_tools = 'Read,Glob,Grep'
            payload = self._run_prompt(
                prompt=normalized_prompt,
                cwd=normalized_cwd,
                additional_dirs=[],
                log_label='triage investigation',
            )
        finally:
            self._disallowed_tools = original_disallowed
            self._allowed_tools = original_allowed
        result_text = payload.get('result') or payload.get(ImplementationFields.MESSAGE) or ''
        return str(result_text)

    def fix_review_comment(
        self,
        comment: ReviewComment,
        branch_name: str,
        session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
    ) -> dict[str, str | bool]:
        prompt = self._build_review_prompt(comment, branch_name)
        cwd = self._review_comment_cwd(comment)
        result = self._run_prompt_result(
            prompt=prompt,
            cwd=cwd,
            additional_dirs=[],
            session_id=session_id,
            branch_name=branch_name,
            default_commit_message='Address review comments',
            log_label=agent_prompt_utils.review_conversation_title(
                comment,
                task_id=task_id,
                task_summary=task_summary,
            ),
        )
        self.logger.info(
            'review fix finished for pull request %s comment %s with success=%s',
            comment.pull_request_id,
            comment.comment_id,
            result[ImplementationFields.SUCCESS],
        )
        return result

    # ----- prompt builders (Claude-specific, share core helpers with KatoClient) -----

    def _build_implementation_prompt(
        self,
        task: Task,
        prepared_task: PreparedTaskContext | None = None,
    ) -> str:
        repository_scope = agent_prompt_utils.repository_scope_text(task, prepared_task)
        return (
            f'Implement task {task.id}: {task.summary}\n\n'
            f'{task.description}\n\n'
            f'{repository_scope}\n\n'
            f'{self._execution_guardrails_text()}\n\n'
            f'{self._completion_instructions_text()}\n\n'
            'The validation_report.md must list every changed file and, under each '
            'file name, add a short explanation of what changed.\n'
            'Use this format inside validation_report.md:\n'
            'Files changed:\n'
            '- path/to/file.ext\n'
            '  Short explanation.\n'
            '- another/file.ext\n'
            '  Short explanation.\n'
        )

    def _build_testing_prompt(
        self,
        task: Task,
        prepared_task: PreparedTaskContext | None = None,
    ) -> str:
        repository_scope = agent_prompt_utils.repository_scope_text(task, prepared_task)
        return (
            f'Validate the implementation for task {task.id}: {task.summary}\n\n'
            f'{task.description}\n\n'
            f'{repository_scope}\n\n'
            f'{self._execution_guardrails_text()}\n\n'
            'Act as a separate testing agent.\n'
            'Write additional tests when needed, challenge the new code with edge cases, '
            'run the relevant tests, and fix any test failures you can resolve safely.\n'
            'Make the smallest possible change needed for the validation work.\n'
            'Prefer editing only the exact lines or blocks that need to change.\n'
            'Do not change indentation, formatting, or unrelated lines when a narrow edit is enough.\n'
            'Do not run npm run build, yarn build, pnpm build, or any equivalent production build command unless the task explicitly requires it.\n'
            'Do not commit or stage generated build artifacts such as build, dist, out, coverage, or target directories.\n'
            'Do not create a pull request.\n'
            f'{self._completion_instructions_text(testing=True)}\n'
            'If no dedicated tests are defined or available, do not invent new ones; '
            'just report that no testing was defined and stop after saving any change.\n'
        )

    @classmethod
    def _build_review_prompt(cls, comment: ReviewComment, branch_name: str) -> str:
        repository_context = agent_prompt_utils.review_repository_context(comment)
        review_context = agent_prompt_utils.review_comment_context_text(comment)
        return (
            f'Address pull request comment on branch {branch_name}{repository_context}.\n'
            f'Comment by {comment.author}: {comment.body}'
            f'{review_context}\n\n'
            f'{cls._execution_guardrails_text()}\n\n'
            'Make the smallest possible change needed to address the review comment.\n'
            'Prefer editing only the exact lines or blocks that need to change.\n'
            'Do not change indentation, formatting, or unrelated lines when a narrow edit is enough.\n'
            'Do not report success until all intended changes are saved in the repository worktree.\n'
            'When you are done, stop. Do not produce any extra commentary.\n'
        )

    def _completion_instructions_text(self, *, testing: bool = False) -> str:
        if testing:
            return (
                'When you are done:\n'
                '- Save every intended change in the repository worktree.\n'
                '- Create validation_report.md in the repository root that summarizes the testing work.\n'
                '- Do not commit or stage validation_report.md; the orchestration layer will read and remove it.\n'
                '- Stop. Do not produce any extra commentary.'
            )
        return (
            'When you are done:\n'
            '- Save every intended change in the repository worktree.\n'
            '- Create validation_report.md in the repository root that will become the pull request description.\n'
            '- Make the smallest possible change needed to satisfy the task.\n'
            '- Prefer editing only the exact lines or blocks that need to change.\n'
            '- Do not change indentation, formatting, or unrelated lines when a narrow edit is enough.\n'
            '- Do not run npm run build, yarn build, pnpm build, or any equivalent production build command unless the task explicitly requires it.\n'
            '- Do not commit or stage generated build artifacts such as build, dist, out, coverage, or target directories.\n'
            '- Do not commit or stage validation_report.md; the orchestration layer will read and remove it before opening the pull request.\n'
            '- If no dedicated tests are defined for this task, do not invent new ones; just stop after saving the change.\n'
            '- Stop. Do not produce any extra commentary.'
        )

    @classmethod
    def _execution_guardrails_text(cls) -> str:
        return f'{agent_prompt_utils.security_guardrails_text()}\n\n{cls._tool_guardrails_text()}'

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
            'KATO handles everything after you finish:\n'
            '- Kato is the orchestrator that spawned you.\n'
            '- Kato sees your file edits on disk and commits them.\n'
            '- Kato pushes the branch.\n'
            '- Kato opens the pull request.\n'
            '- This is automatic. The operator does NOT need to allow anything, run anything, or click anything for git to happen.\n'
            '\n'
            'When you finish editing, your reply must be exactly one short sentence: "Done — edits written, kato will publish."  If you genuinely have nothing more to say, that one line is the entire reply.\n'
            '\n'
            'Do NOT say things like "I am ready to commit when you allow git access" or "let me know when I can push" or any variation. Those are wrong because there is nothing for the operator to allow — kato runs git automatically the moment your turn ends.'
        )

    # ----- subprocess execution -----

    def _run_prompt_result(
        self,
        *,
        prompt: str,
        cwd: str,
        additional_dirs: list[str],
        branch_name: str = '',
        default_commit_message: str | None = None,
        session_id: str = '',
        log_label: str = '',
    ) -> dict[str, str | bool]:
        payload = self._run_prompt(
            prompt=prompt,
            cwd=cwd,
            additional_dirs=additional_dirs,
            session_id=session_id,
            log_label=log_label,
        )
        return build_openhands_result(
            payload,
            branch_name=branch_name,
            default_commit_message=default_commit_message,
        )

    def _run_prompt(
        self,
        *,
        prompt: str,
        cwd: str,
        additional_dirs: list[str],
        session_id: str = '',
        log_label: str = '',
    ) -> dict[str, str | bool]:
        command = self._build_command(
            additional_dirs=additional_dirs,
            session_id=session_id,
        )
        env = self._build_subprocess_env()
        log_label = log_label or 'Claude CLI'
        self.logger.info('Mission %s: invoking Claude CLI', log_label)
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                cwd=cwd or None,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
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
        session_id: str,
    ) -> list[str]:
        command: list[str] = [
            self._binary,
            '-p',
            '--output-format',
            'json',
            '--permission-mode',
            self._permission_mode,
        ]
        if self._model:
            command.extend(['--model', self._model])
        if self._max_turns is not None:
            command.extend(['--max-turns', str(self._max_turns)])
        if self._effort:
            command.extend(['--effort', self._effort])
        if self._allowed_tools:
            command.extend(['--allowedTools', self._allowed_tools])
        merged_disallowed = self._merge_disallowed_with_git_deny(self._disallowed_tools)
        command.extend(['--disallowedTools', merged_disallowed])
        architecture_doc = read_architecture_doc(
            self._architecture_doc_path, logger=self.logger,
        )
        if architecture_doc:
            command.extend(['--append-system-prompt', architecture_doc])
        normalized_session_id = normalized_text(session_id)
        if normalized_session_id:
            command.extend(['--resume', normalized_session_id])
        for directory in additional_dirs:
            normalized_dir = normalized_text(directory)
            if normalized_dir:
                command.extend(['--add-dir', normalized_dir])
        command.extend(self._extra_args)
        return command

    @classmethod
    def _merge_disallowed_with_git_deny(cls, operator_disallowed: str) -> str:
        """Always include the git denylist, regardless of operator config.

        The operator can extend the denylist via ``KATO_CLAUDE_DISALLOWED_TOOLS``
        but cannot remove the git patterns. Kato is the sole component that
        runs git operations.
        """
        existing = [
            entry.strip()
            for entry in (operator_disallowed or '').split(',')
            if entry.strip()
        ]
        seen = {entry: True for entry in existing}
        for pattern in cls.GIT_DENY_PATTERNS:
            if pattern not in seen:
                existing.append(pattern)
                seen[pattern] = True
        return ','.join(existing)

    def _build_subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        # Force JSON output to stdout and prevent any TTY-dependent behavior.
        env.setdefault('CLAUDE_CODE_NONINTERACTIVE', '1')
        return env

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
        session_id_value = normalized_text(payload.get('session_id', ''))

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

        result: dict[str, str | bool] = {
            ImplementationFields.SUCCESS: success,
            Task.summary.key: result_text,
        }
        if result_text:
            result[ImplementationFields.MESSAGE] = result_text
        if session_id_value:
            result[ImplementationFields.SESSION_ID] = session_id_value
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

    def _working_directories(
        self,
        prepared_task: PreparedTaskContext | None,
    ) -> tuple[str, list[str]]:
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

    def _review_comment_cwd(self, comment: ReviewComment) -> str:
        repository_local_path = normalized_text(
            text_from_attr(comment, 'repository_local_path')
        )
        if repository_local_path:
            return repository_local_path
        if self._repository_root_path:
            return self._repository_root_path
        return os.getcwd()

    # ----- smoke test -----

    def _validate_model_smoke_test(self) -> None:
        if not self._model_smoke_test_enabled:
            return
        self._validate_model_access_smoke_test()

    def _validate_model_access_smoke_test(self) -> None:
        if self._model_access_smoke_test_ran:
            return
        self._run_model_access_validation()
        self._model_access_smoke_test_ran = True

    def _run_model_access_validation(self) -> None:
        self.logger.info('running Claude CLI model access validation')
        command = self._build_command(additional_dirs=[], session_id='')
        env = self._build_subprocess_env()
        try:
            completed = subprocess.run(
                command,
                input=self.SMOKE_TEST_PROMPT,
                cwd=self._repository_root_path or None,
                env=env,
                capture_output=True,
                text=True,
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

    @staticmethod
    def _coerce_max_turns(value: int | str | None) -> int | None:
        if value is None or value == '':
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        if parsed <= 0:
            return None
        return parsed

    @classmethod
    def _coerce_effort(cls, value: str | None) -> str:
        """Validate the ``--effort`` value so we fail at startup, not mid-turn.

        Accepted: ``low``, ``medium``, ``high``, ``xhigh``, ``max``. Empty
        string means "don't pass --effort" (Claude uses its default).
        Anything else is rejected so a typo doesn't silently regress
        reasoning quality on production tasks.
        """
        normalized = normalized_text(value).lower()
        if not normalized:
            return ''
        if normalized not in cls.SUPPORTED_EFFORT_LEVELS:
            raise ValueError(
                f'invalid claude effort {value!r}; '
                f'expected one of {sorted(cls.SUPPORTED_EFFORT_LEVELS)} or empty'
            )
        return normalized
