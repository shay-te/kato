"""Drive xAI's Grok Build CLI (``grok -p``) as an orchestrator agent backend.

Same public surface as every other CLI client, so the orchestration layer can
use any backend interchangeably. The spawn-free half shared with the other
per-turn CLIs lives in ``agent_core_lib.per_turn_cli_shared``; what stays here
is everything that starts a process — the version probe, the spawn, the smoke
test — and Grok's argv (``session/wire_protocol.py``).

Grok-specific, from Grok Build's source and user guide:

* the prompt goes in a file (``--prompt-file``): headless Grok never reads
  stdin;
* output is ``--output-format streaming-messages-json`` (see
  ``session/wire_protocol.py`` for why not the native ``streaming-json``);
* the system addendum rides in ``--rules`` (Grok's ``--append-system-prompt``)
  rather than in the prompt body;
* ``allowed_tools`` / ``disallowed_tools`` pass through as Grok ``--allow`` /
  ``--deny`` rules (Grok takes Claude-style rules), and the command floor is
  ALWAYS added as ``--deny`` rules — deny beats every approval mode;
* there is no Docker sandbox image for Grok, so Docker mode and bypass are
  REFUSED rather than run unsandboxed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Any

from agent_core_lib.agent_core_lib.helpers.cancellable_process import (
    ProcessCancelled,
    run_cancellable,
)
from agent_core_lib.agent_core_lib.helpers.lessons_doc_utils import read_lessons_file
from agent_core_lib.agent_core_lib.per_turn_cli_shared import PerTurnCliAgentBehaviour
from grok_core_lib.grok_core_lib.session.wire_protocol import (
    BASE_FLAGS,
    EFFORT_LEVELS,
    OUTPUT_FORMAT,
    SANDBOX_READ_ONLY,
    SANDBOX_WORKSPACE,
    deny_rules,
    permission_arguments,
    translate_event,
)
from agent_core_lib.agent_core_lib.helpers.working_directory_utils import (
    single_project_root,
)
from sandbox_core_lib.sandbox_core_lib.workspace_delimiter import (
    wrap_untrusted_workspace_content,
)
from utils_core_lib.utils_core_lib.text_utils import condensed_text, normalized_text

_NO_SANDBOX_IMAGE = (
    'Grok has no Docker sandbox image, so the Docker sandbox and '
    'bypass-permissions mode are refused for it rather than run unsandboxed. '
    'Turn them off for Grok, or use Claude for sandboxed runs.'
)


class GrokCliClient(PerTurnCliAgentBehaviour):
    """Drive the Grok Build CLI as the implementation/testing backend."""

    DEFAULT_BINARY = 'grok'
    CLI_DISPLAY_NAME = 'Grok'
    DEFAULT_TIMEOUT_SECONDS = 1800
    SUPPORTED_EFFORT_LEVELS = frozenset(EFFORT_LEVELS)
    BYPASS_WARNING = (
        'Bypass-permissions mode is set for Grok, which has no Docker sandbox '
        'image: every Grok run will be REFUSED until it is turned off.'
    )
    IGNORED_KNOBS = ('read_only_tools_on',)
    IGNORED_KNOBS_HINT = (
        'the read-only tool allowlist needs the Docker sandbox, which Grok '
        'does not have; reads need no approval in Grok anyway.'
    )
    SUBPROCESS_ENV = {'GROK_DISABLE_AUTOUPDATER': '1'}

    # ----- health checks (spawn) -------------------------------------------

    def validate_connection(self) -> None:
        if self._running_inside_docker():
            raise RuntimeError(
                'The Grok backend is not supported inside Docker. The Grok CLI '
                'authenticates against your host credentials (``grok login`` '
                'writes to ``~/.grok`` on the host), and the container cannot '
                'reach those. Run the agent on the host instead.'
            )
        if self._docker_mode_on or self._bypass_permissions:
            raise RuntimeError(_NO_SANDBOX_IMAGE)
        binary_path = shutil.which(self._binary)
        if not binary_path:
            raise RuntimeError(
                f'\n'
                f'Grok CLI ("{self._binary}") was not found on PATH.\n'
                f'\n'
                f'Install Grok Build:\n'
                f'\n'
                f'    macOS / Linux / WSL:  curl -fsSL https://x.ai/cli/install.sh | bash\n'
                f'    Windows (PowerShell): irm https://x.ai/cli/install.ps1 | iex\n'
                f'\n'
                f'Then sign in once with ``grok login`` (or set XAI_API_KEY) and\n'
                f'check with ``grok --version``. If you installed it somewhere\n'
                f'else, set the Grok binary path in Settings.\n'
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
                # Never the operator's terminal: given a TTY a CLI can go raw,
                # and a timeout kill leaves it raw.
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(
                f'Grok CLI binary "{self._binary}" failed to launch: {exc}'
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip() or 'unknown error'
            raise RuntimeError(
                f'Grok CLI binary "{self._binary}" failed to report a version: {detail}'
            )
        version_text = condensed_text(result.stdout)
        self.logger.info('Grok CLI is available at %s (%s)', binary_path, version_text)
        self._validate_streaming_support(binary_path, version_text)
        self._validate_model_smoke_test()

    def _validate_streaming_support(self, binary_path: str, version_text: str) -> None:
        """Refuse a CLI too old to print the stream this transport parses.

        Feature-detected: the format is the thing depended on. A help probe
        that cannot run is not a failure — a sandbox that blocks it must not
        make a working CLI look broken.
        """
        try:
            probe = subprocess.run(
                [*self._host_binary_argv(), '--help'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                check=False,
                timeout=self.VERSION_PROBE_TIMEOUT_SECONDS,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            return
        if probe.returncode != 0:
            return
        if OUTPUT_FORMAT in (probe.stdout or '') + (probe.stderr or ''):
            return
        raise RuntimeError(
            f'\n'
            f'The Grok CLI at {binary_path} is too old for streaming chat.\n'
            f'\n'
            f'    reported version: {version_text or "unknown"}\n'
            f'\n'
            f'Each run is read with `--output-format {OUTPUT_FORMAT}`, which\n'
            f'this build does not offer. Upgrade it with `grok update`, then\n'
            f'check with `grok --version`.\n'
        )

    def _wrap_untrusted(self, text: str, *, source_path: str) -> str:
        """Bind the shared prompt scaffolding to the delimiter framing."""
        return wrap_untrusted_workspace_content(text, source_path=source_path)

    @classmethod
    def _wrap_untrusted_text(cls, text: str, *, source_path: str) -> str:
        """Bind the shared prompt scaffolding to the delimiter framing."""
        return wrap_untrusted_workspace_content(text, source_path=source_path)

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
        cancel_event: threading.Event | None = None,
        timeout_seconds: int = 0,
        model: str = '',
    ) -> dict[str, str | bool]:
        del task_id  # no per-task container: Docker mode is refused for Grok
        if self._docker_mode_on or self._bypass_permissions:
            raise RuntimeError(_NO_SANDBOX_IMAGE)
        effective_timeout = int(timeout_seconds) if timeout_seconds else self._timeout_seconds
        folder = tempfile.mkdtemp(prefix='grok-run-')
        try:
            prompt_file = os.path.join(folder, 'prompt.md')
            with open(prompt_file, 'w', encoding='utf-8') as handle:
                handle.write(prompt)
            command = self._build_command(
                additional_dirs=additional_dirs,
                agent_session_id=agent_session_id,
                prompt_file=prompt_file,
                cwd=cwd,
                sandbox_override=sandbox_override,
                model=model,
            )
            env = self._build_subprocess_env()
            log_label = log_label or 'Grok CLI'
            self.logger.info('Mission %s: invoking Grok CLI', log_label)
            try:
                if cancel_event is None:
                    completed = subprocess.run(
                        command,
                        cwd=cwd or None,
                        env=env,
                        capture_output=True,
                        text=True,
                        encoding='utf-8',
                        errors='replace',
                        check=False,
                        timeout=effective_timeout,
                        stdin=subprocess.DEVNULL,
                    )
                else:
                    completed = run_cancellable(
                        command,
                        input_text='',
                        cwd=cwd or None,
                        env=env,
                        timeout_seconds=effective_timeout,
                        cancel_event=cancel_event,
                        logger=self.logger,
                        label=log_label,
                    )
            except ProcessCancelled:
                raise
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError(
                    f'Grok CLI did not finish within {effective_timeout}s for {log_label}'
                ) from exc
            except OSError as exc:
                raise RuntimeError(
                    f'failed to invoke Grok CLI binary "{self._binary}": {exc}'
                ) from exc
            return self._parse_completed_process(completed, log_label=log_label)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def _build_command(
        self,
        *,
        additional_dirs: list[str],
        agent_session_id: str,
        resolve_binary: bool = True,
        include_system_prompt: bool = True,
        prompt_file: str = '',
        cwd: str = '',
        sandbox_override: str = '',
        model: str = '',
    ) -> list[str]:
        """The argv for one headless ``grok`` run (fresh, or ``--resume <id>``).

        An explicit ``sandbox_override`` (a read-only run) outranks everything
        else, for this run only. A resumed session keeps the working directory
        it started in, so ``--cwd`` goes on a fresh run only.
        """
        prefix = self._host_binary_argv() if resolve_binary else [self._binary]
        command = [*prefix, *BASE_FLAGS]
        if prompt_file:
            command.extend(['--prompt-file', prompt_file])
        session_id = normalized_text(agent_session_id)
        if session_id:
            command.extend(['--resume', session_id])
        elif normalized_text(cwd):
            command.extend(['--cwd', single_project_root(normalized_text(cwd), additional_dirs)])
        chosen_model = normalized_text(model) or self._model
        if chosen_model:
            command.extend(['-m', chosen_model])
        if self._effort:
            command.extend(['--reasoning-effort', self._effort])
        if self._max_turns is not None:
            command.extend(['--max-turns', str(self._max_turns)])
        read_only = normalized_text(sandbox_override) == SANDBOX_READ_ONLY
        command.extend(permission_arguments(
            sandbox=SANDBOX_READ_ONLY if read_only else SANDBOX_WORKSPACE,
        ))
        for rule in _rules(self._allowed_tools):
            command.extend(['--allow', rule])
        for rule in _rules(self._disallowed_tools):
            command.extend(['--deny', rule])
        # One-shot runs are always strict (only a chat follows the setting).
        command.extend(deny_rules())
        if include_system_prompt:
            rules = _system_rules(
                self._lessons_path, docker_mode_on=self._docker_mode_on, logger=self.logger,
            )
            if rules:
                command.extend(['--rules', rules])
        command.extend(self._extra_args)
        return command

    def _translate_wire_event(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        return translate_event(raw)

    # ----- smoke test -----

    def _run_model_access_validation(self) -> None:
        self.logger.info('running Grok CLI model access validation')
        folder = tempfile.mkdtemp(prefix='grok-smoke-')
        try:
            prompt_file = os.path.join(folder, 'prompt.md')
            with open(prompt_file, 'w', encoding='utf-8') as handle:
                handle.write(self.SMOKE_TEST_PROMPT)
            command = self._build_command(
                additional_dirs=[], agent_session_id='',
                include_system_prompt=False, prompt_file=prompt_file,
                sandbox_override=SANDBOX_READ_ONLY,
            )
            try:
                completed = subprocess.run(
                    command,
                    cwd=self._repository_root_path or None,
                    env=self._build_subprocess_env(),
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    check=False,
                    timeout=self.SMOKE_TEST_TIMEOUT_SECONDS,
                    stdin=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f'Grok CLI smoke test did not finish within {self.SMOKE_TEST_TIMEOUT_SECONDS}s'
                ) from exc
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        failure = self._smoke_test_failure(completed)
        if failure:
            raise RuntimeError(failure)


def _rules(value: str) -> list[str]:
    """A comma-separated rule list, split on commas OUTSIDE parentheses
    (``Bash(git diff, git log)`` is one rule)."""
    rules: list[str] = []
    depth = 0
    current = ''
    for character in str(value or ''):
        if character == '(':
            depth += 1
        elif character == ')':
            depth = max(0, depth - 1)
        if character == ',' and depth == 0:
            rules.append(current)
            current = ''
            continue
        current += character
    rules.append(current)
    return [rule.strip() for rule in rules if re.search(r'\S', rule)]


def _system_rules(lessons_path: str, *, docker_mode_on: bool, logger) -> str:
    """The host's system addendum (the lessons directive, the
    untrusted-content explanation), delivered as Grok ``--rules`` — the system
    channel Codex lacks."""
    from sandbox_core_lib.sandbox_core_lib.system_prompt import compose_system_prompt
    lessons_text = read_lessons_file(lessons_path, logger=logger)
    return compose_system_prompt(docker_mode_on=docker_mode_on, lessons=lessons_text)
