"""Drive the OpenCode CLI (``opencode run``) as an orchestrator agent backend.

Same public surface as every other CLI client, so the orchestration layer can
use any backend interchangeably. The spawn-free half shared with the other
per-turn CLIs lives in ``agent_core_lib.per_turn_cli_shared``; what stays here
is everything that starts a process — the version probe, the spawn, the smoke
test — and OpenCode's argv (``session/wire_protocol.py``).

OpenCode-specific, from OpenCode's source (V2 and V1):

* the prompt goes on stdin (``opencode run`` with no message reads it all);
* output is ``--format json``; there is no done event — exit 0 with no
  ``error`` line is success;
* there is no system-prompt flag, so the system addendum rides in the prompt
  body (as for Codex);
* permission rules — edits allowed, the command floor denied, nothing outside
  the project — go inline in ``OPENCODE_CONFIG_CONTENT``, and ``$PWD`` names
  the project (OpenCode reads it before its own working directory);
* V2 needs ``--standalone`` so the run's environment reaches the server that
  executes it (``helpers/cli_generation.py``);
* there is no Docker sandbox image for OpenCode, so Docker mode and bypass are
  REFUSED rather than run unsandboxed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from typing import Any

from agent_core_lib.agent_core_lib.helpers.cancellable_process import (
    ProcessCancelled,
    run_cancellable,
)
from agent_core_lib.agent_core_lib.helpers.lessons_doc_utils import read_lessons_file
from agent_core_lib.agent_core_lib.helpers.working_directory_utils import (
    single_project_root,
)
from agent_core_lib.agent_core_lib.per_turn_cli_shared import PerTurnCliAgentBehaviour
from opencode_core_lib.opencode_core_lib.helpers.cli_generation import cli_generation
from opencode_core_lib.opencode_core_lib.session.wire_protocol import (
    EFFORT_LEVELS,
    MODE_READ_ONLY,
    MODE_WORKSPACE,
    model_arguments,
    run_arguments,
    run_environment,
    translate_event,
)
from sandbox_core_lib.sandbox_core_lib.workspace_delimiter import (
    wrap_untrusted_workspace_content,
)
from utils_core_lib.utils_core_lib.text_utils import condensed_text, normalized_text

_NO_SANDBOX_IMAGE = (
    'OpenCode has no Docker sandbox image, so the Docker sandbox and '
    'bypass-permissions mode are refused for it rather than run unsandboxed. '
    'Turn them off for OpenCode, or use Claude for sandboxed runs.'
)


class OpenCodeCliClient(PerTurnCliAgentBehaviour):
    """Drive the OpenCode CLI as the implementation/testing backend."""

    DEFAULT_BINARY = 'opencode'
    CLI_DISPLAY_NAME = 'OpenCode'
    DEFAULT_TIMEOUT_SECONDS = 1800
    SUPPORTED_EFFORT_LEVELS = frozenset(EFFORT_LEVELS)
    BYPASS_WARNING = (
        'Bypass-permissions mode is set for OpenCode, which has no Docker '
        'sandbox image: every OpenCode run will be REFUSED until it is turned off.'
    )
    IGNORED_KNOBS = ('allowed_tools', 'disallowed_tools', 'max_turns', 'read_only_tools_on')
    IGNORED_KNOBS_HINT = (
        'OpenCode has no per-run tool lists or turn cap; its permission rules '
        'are set by the host (the command floor) and its own config.'
    )

    # ----- health checks (spawn) -------------------------------------------

    def validate_connection(self) -> None:
        if self._running_inside_docker():
            raise RuntimeError(
                'The OpenCode backend is not supported inside Docker. The '
                'OpenCode CLI authenticates against your host credentials '
                '(``opencode auth login`` writes to ``~/.local/share/opencode`` '
                'on the host), and the container cannot reach those. Run the '
                'agent on the host instead.'
            )
        if self._docker_mode_on or self._bypass_permissions:
            raise RuntimeError(_NO_SANDBOX_IMAGE)
        binary_path = shutil.which(self._binary)
        if not binary_path:
            raise RuntimeError(
                f'\n'
                f'OpenCode CLI ("{self._binary}") was not found on PATH.\n'
                f'\n'
                f'Install OpenCode (works on macOS, Linux, and Windows):\n'
                f'\n'
                f'    npm install -g @opencode/cli        (V2)\n'
                f'    npm install -g opencode-ai          (V1)\n'
                f'    curl -fsSL https://opencode.ai/v2/install | bash\n'
                f'\n'
                f'Then connect a provider once with ``opencode auth login`` and\n'
                f'check with ``opencode --version``. If you installed it\n'
                f'somewhere else, set the OpenCode binary path in Settings.\n'
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
                f'OpenCode CLI binary "{self._binary}" failed to launch: {exc}'
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip() or 'unknown error'
            raise RuntimeError(
                f'OpenCode CLI binary "{self._binary}" failed to report a version: {detail}'
            )
        version_text = condensed_text(result.stdout)
        self.logger.info('OpenCode CLI is available at %s (%s)', binary_path, version_text)
        self._validate_streaming_support(binary_path, version_text)
        self._validate_model_smoke_test()

    def _validate_streaming_support(self, binary_path: str, version_text: str) -> None:
        """Refuse a CLI whose ``run`` cannot print the JSON event stream.

        Feature-detected from ``opencode run --help``. A help probe that
        cannot run is not a failure.
        """
        try:
            probe = subprocess.run(
                [*self._host_binary_argv(), 'run', '--help'],
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
        if '--format' in (probe.stdout or '') + (probe.stderr or ''):
            return
        raise RuntimeError(
            f'\n'
            f'The OpenCode CLI at {binary_path} is too old for streaming chat.\n'
            f'\n'
            f'    reported version: {version_text or "unknown"}\n'
            f'\n'
            f'Each run is read with `opencode run --format json`, which this\n'
            f'build does not offer. Upgrade it (`opencode upgrade`, or\n'
            f'reinstall it), then check with `opencode --version`.\n'
        )

    def _wrap_untrusted(self, text: str, *, source_path: str) -> str:
        """Bind the shared prompt scaffolding to the delimiter framing."""
        return wrap_untrusted_workspace_content(text, source_path=source_path)

    @classmethod
    def _wrap_untrusted_text(cls, text: str, *, source_path: str) -> str:
        """Bind the shared prompt scaffolding to the delimiter framing."""
        return wrap_untrusted_workspace_content(text, source_path=source_path)

    def _system_prompt_addendum(self) -> str:
        """OpenCode has no system-prompt flag, so the lessons directive and the
        untrusted-content explanation ride in the prompt body, as for Codex."""
        lessons_text = read_lessons_file(self._lessons_path, logger=self.logger)
        from sandbox_core_lib.sandbox_core_lib.system_prompt import compose_system_prompt
        return compose_system_prompt(
            docker_mode_on=self._docker_mode_on,
            lessons=lessons_text,
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
        cancel_event: threading.Event | None = None,
        timeout_seconds: int = 0,
        model: str = '',
    ) -> dict[str, str | bool]:
        del task_id  # no per-task container: Docker mode is refused for OpenCode
        if self._docker_mode_on or self._bypass_permissions:
            raise RuntimeError(_NO_SANDBOX_IMAGE)
        effective_timeout = int(timeout_seconds) if timeout_seconds else self._timeout_seconds
        command = self._build_command(
            additional_dirs=additional_dirs,
            agent_session_id=agent_session_id,
            model=model,
        )
        read_only = normalized_text(sandbox_override) == MODE_READ_ONLY
        env = self._build_subprocess_env()
        env.update(run_environment(
            single_project_root(cwd, additional_dirs),
            mode=MODE_READ_ONLY if read_only else MODE_WORKSPACE,
        ))
        log_label = log_label or 'OpenCode CLI'
        self.logger.info('Mission %s: invoking OpenCode CLI', log_label)
        try:
            if cancel_event is None:
                completed = subprocess.run(
                    command,
                    input=prompt,
                    cwd=cwd or None,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    check=False,
                    timeout=effective_timeout,
                )
            else:
                completed = run_cancellable(
                    command,
                    input_text=prompt,
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
                f'OpenCode CLI did not finish within {effective_timeout}s for {log_label}'
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f'failed to invoke OpenCode CLI binary "{self._binary}": {exc}'
            ) from exc
        return self._parse_completed_process(completed, log_label=log_label)

    def _build_command(
        self,
        *,
        additional_dirs: list[str],
        agent_session_id: str,
        resolve_binary: bool = True,
        include_system_prompt: bool = True,  # accepted for parity: no system flag
        model: str = '',
    ) -> list[str]:
        """The argv for one ``opencode run`` (fresh, or ``--session <id>``).

        Where the run works and what it may do are not argv here: the project
        is ``$PWD`` and the rules are in the environment (``_run_prompt``).
        """
        del additional_dirs, include_system_prompt  # see docstring
        prefix = self._host_binary_argv() if resolve_binary else [self._binary]
        generation = cli_generation(self._binary_path or self._binary)
        command = run_arguments(prefix, generation)
        session_id = normalized_text(agent_session_id)
        if session_id:
            command.extend(['--session', session_id])
        chosen_model = normalized_text(model) or self._model
        command.extend(model_arguments(chosen_model, self._effort, generation))
        command.extend(self._extra_args)
        return command

    def _translate_wire_event(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        return translate_event(raw)

    # ----- smoke test -----

    def _run_model_access_validation(self) -> None:
        self.logger.info('running OpenCode CLI model access validation')
        command = self._build_command(
            additional_dirs=[], agent_session_id='', include_system_prompt=False,
        )
        env = self._build_subprocess_env()
        env.update(run_environment(
            self._repository_root_path or os.getcwd(), mode=MODE_READ_ONLY,
        ))
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
                f'OpenCode CLI smoke test did not finish within {self.SMOKE_TEST_TIMEOUT_SECONDS}s'
            ) from exc
        failure = self._smoke_test_failure(completed)
        if failure:
            raise RuntimeError(failure)
