"""Drive OpenAI's Codex CLI (``codex exec``) as an orchestrator agent backend.

Same public surface as every other CLI client, so the orchestration layer can
use any backend interchangeably. Codex is shaped differently from Claude Code
under the hood (sandbox modes instead of an allow/deny tool list, a
``resume <id>`` subcommand instead of a ``--resume`` flag, JSONL events
instead of one JSON object); the spawn-free half shared with the other
per-turn CLIs lives in ``agent_core_lib.per_turn_cli_shared``. What stays here
is everything that starts a process — the version probe, the sandboxed spawn,
the smoke test — and Codex's argv.

Verified against ``codex-cli 0.132.0`` (the OpenAI ``@openai/codex``
package). When the CLI version moves, re-read ``codex exec --help`` and adjust
``_build_command`` if a flag has changed.
"""

from __future__ import annotations

import os
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
from codex_core_lib.codex_core_lib.session.wire_protocol import translate_event
from sandbox_core_lib.sandbox_core_lib.workspace_delimiter import (
    wrap_untrusted_workspace_content,
)
from utils_core_lib.utils_core_lib.text_utils import (
    condensed_text,
    normalized_text,
)


class CodexCliClient(PerTurnCliAgentBehaviour):
    """Drive OpenAI's Codex CLI as the implementation/testing backend.

    The public methods match every other client one-to-one so callers do not
    branch on backend. The differences live in ``_build_command`` (Codex CLI
    flag set) and the spawn itself (``--output-last-message`` file, Docker
    sandbox wrap).

    ``max_turns``, ``effort``, ``allowed_tools``, ``disallowed_tools`` and
    ``read_only_tools_on`` are accepted for parity but have no Codex flag —
    one info line at construction says so.
    """

    DEFAULT_BINARY = 'codex'
    CLI_DISPLAY_NAME = 'Codex'
    DEFAULT_TIMEOUT_SECONDS = 1800
    # Codex 0.132 has NO ``--ask-for-approval`` flag on ``codex exec`` — that
    # flag is only on the top-level interactive ``codex`` command. For the
    # non-interactive path the approval policy comes from
    # ``~/.codex/config.toml`` (``approval_policy``) or a ``-c`` override; the
    # real safety boundary in safe mode is ``--sandbox``. ``workspace-write``
    # lets the agent edit inside the workspace tree and blocks writes
    # elsewhere; ``danger-full-access`` is never set automatically.
    SAFE_SANDBOX_MODE = 'workspace-write'
    # Accepted for parity but not translated to a flag — Codex routes
    # reasoning depth via ``model_reasoning_effort`` in its config file.
    SUPPORTED_EFFORT_LEVELS = frozenset({'low', 'medium', 'high', 'xhigh', 'max'})
    BYPASS_WARNING = (
        'Bypass-permissions mode is enabled: Codex will run with '
        '--dangerously-bypass-approvals-and-sandbox. Per-tool '
        'prompts and sandbox containment are BOTH disabled — the '
        'agent can run shell, edit, write, and any other tool '
        'against any path it can reach. The operator who enabled '
        'this accepts responsibility for any harm caused by the '
        'agent. See SECURITY.md.'
    )
    IGNORED_KNOBS = (
        'allowed_tools', 'disallowed_tools', 'max_turns', 'effort',
        'read_only_tools_on',
    )
    IGNORED_KNOBS_HINT = (
        'those are Claude Code concepts with no Codex CLI 0.132.x equivalent. '
        'Use --sandbox mode (auto-set by the host) and ~/.codex/config.toml '
        'for similar controls.'
    )

    # ----- health checks (spawn) -------------------------------------------

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
                # Never the operator's terminal: given a TTY the CLI goes raw, and a
                # timeout kill leaves it raw — Ctrl+C then can't stop the host.
                stdin=subprocess.DEVNULL,
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
                # Never the operator's terminal: given a TTY the CLI goes raw, and a
                # timeout kill leaves it raw — Ctrl+C then can't stop the host.
                stdin=subprocess.DEVNULL,
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

    def _system_prompt_addendum(self) -> str:
        """Codex has no ``--append-system-prompt`` flag, so the
        lessons text is prepended to the user
        prompt instead. Same payload Claude gets through
        ``--append-system-prompt``."""
        lessons_text = read_lessons_file(
            self._lessons_path, logger=self.logger,
        )
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
        # 0 = the client's configured default. A per-call override lets a long
        # read-only run (a big task's review) get more than the default.
        effective_timeout = int(timeout_seconds) if timeout_seconds else self._timeout_seconds
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
                model=model,
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
                if cancel_event is None:
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
                        timeout=effective_timeout,
                    )
                else:
                    completed = run_cancellable(
                        command,
                        input_text=prompt,
                        cwd=spawn_cwd,
                        env=env,
                        timeout_seconds=effective_timeout,
                        cancel_event=cancel_event,
                        logger=self.logger,
                        label=log_label,
                    )
            except ProcessCancelled:
                if container_name:
                    # Killing the ``docker run`` client never reaches the
                    # container; stop it explicitly, as the timeout path does.
                    from sandbox_core_lib.sandbox_core_lib.manager import kill_container
                    kill_container(container_name, logger=self.logger)
                raise
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
                    f'Codex CLI did not finish within {effective_timeout}s for {log_label}'
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
        model: str = '',
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
        # An explicit ``sandbox_override`` (a read-only run) outranks bypass:
        # the caller asked for THAT sandbox, for this run only.
        if self._bypass_permissions and not normalized_text(sandbox_override):
            command.append('--dangerously-bypass-approvals-and-sandbox')
        elif not is_resume:
            # --sandbox is ONLY accepted on fresh ``codex exec``; the
            # resume subcommand inherits sandbox mode from the
            # session being resumed.
            sandbox = normalized_text(sandbox_override) or self.SAFE_SANDBOX_MODE
            command.extend(['--sandbox', sandbox])

        # A per-run model (the review loop's pick) outranks the client's.
        chosen_model = normalized_text(model) or self._model
        if chosen_model:
            command.extend(['-m', chosen_model])

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

    def _translate_wire_event(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        return translate_event(raw)

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
        failure = self._smoke_test_failure(completed)
        if failure:
            raise RuntimeError(failure)
