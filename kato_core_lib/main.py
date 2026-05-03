from __future__ import annotations

import os
import signal
import time

import hydra
from omegaconf import DictConfig

from kato_core_lib.helpers import agent_prompt_utils
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.shell_status_utils import (
    sleep_with_countdown_spinner,
    supports_inline_status,
    sleep_with_warmup_countdown,
)
from kato_core_lib.helpers.status_broadcaster_utils import (
    StatusBroadcaster,
    install_status_broadcast_handler,
)
from kato_core_lib.validate_env import validate_environment
from kato_core_lib.validation.bypass_permissions_validator import (
    BypassPermissionsRefused,
    print_security_posture,
    validate_bypass_permissions,
)


_STATUS_BROADCASTER = StatusBroadcaster()
install_status_broadcast_handler(_STATUS_BROADCASTER)


class _ProcessAssignedTasksJobProxy:
    def __call__(self):
        from kato_core_lib.jobs.process_assigned_tasks import ProcessAssignedTasksJob as _ProcessAssignedTasksJob

        return _ProcessAssignedTasksJob()


class _KatoInstanceProxy:
    @staticmethod
    def init(core_lib_cfg: DictConfig) -> None:
        from kato_core_lib.kato_instance import KatoInstance as _KatoInstance

        _KatoInstance.init(core_lib_cfg)

    @staticmethod
    def get():
        from kato_core_lib.kato_instance import KatoInstance as _KatoInstance

        return _KatoInstance.get()


ProcessAssignedTasksJob = _ProcessAssignedTasksJobProxy()
KatoInstance = _KatoInstanceProxy()


@hydra.main(
    version_base=None,
    config_path='config',
    config_name='kato_core_lib',
)
def main(cfg: DictConfig) -> int:
    logger = configure_logger(cfg.core_lib.app.name)
    try:
        validate_environment(mode='all')
    except ValueError as exc:
        logger.error('%s', exc)
        return 1
    try:
        validate_bypass_permissions()
    except BypassPermissionsRefused as exc:
        logger.error('%s', exc)
        return 1
    # Docker mode wraps every Claude spawn in the hardened sandbox
    # (workspace bind-mount only, default-DROP egress firewall,
    # capability drop, read-only rootfs, audit log). When the operator
    # opts into it via ``KATO_CLAUDE_DOCKER=true``, the Docker daemon
    # MUST be available — falling back to host execution silently
    # would defeat the point of the flag. The same is true for
    # ``KATO_CLAUDE_BYPASS_PERMISSIONS=true`` (which requires docker
    # by the constraint enforced in ``validate_bypass_permissions``);
    # by the time this gate runs, bypass-without-docker has already
    # been refused, so checking ``is_docker_mode_enabled()`` alone
    # is sufficient.
    from kato_core_lib.validation.bypass_permissions_validator import is_docker_mode_enabled
    if is_docker_mode_enabled():
        from kato_core_lib.sandbox.manager import (
            check_docker_or_exit,
            check_gvisor_or_exit,
            docker_running_rootless,
            gvisor_runtime_available,
        )
        check_docker_or_exit()
        # gVisor is required by default for any docker-mode spawn —
        # refuses to start without it unless the operator explicitly
        # accepts the residual via KATO_SANDBOX_ALLOW_NO_GVISOR=true.
        # The check applies regardless of bypass; docker-only mode
        # gets the same kernel-CVE-isolation floor as the original
        # bypass mode.
        check_gvisor_or_exit()
        if gvisor_runtime_available():
            logger.info(
                'sandbox: gVisor (runsc) runtime detected — using it '
                'for syscall-level isolation on top of namespaces',
            )
        else:
            logger.warning(
                'sandbox: starting WITHOUT gVisor (operator override '
                'KATO_SANDBOX_ALLOW_NO_GVISOR=true). Container relies '
                'on the host kernel for isolation; a kernel CVE could '
                'be used to escape. Other 8 sandbox layers still apply.',
            )
        if not docker_running_rootless():
            logger.info(
                'sandbox: Docker daemon is running in rooted mode. For '
                'stricter isolation (a container escape stays in your '
                'user account, not full root on the host) consider '
                'rootless Docker: https://docs.docker.com/engine/security/rootless/',
            )
    print_security_posture()
    try:
        KatoInstance.init(cfg)
    except RuntimeError as exc:
        if str(exc).startswith('startup dependency validation failed:') or str(exc).startswith('[Error] '):
            logger.error('%s', exc)
            return 1
        raise
    app = KatoInstance.get()
    app.logger = getattr(app, 'logger', None) or logger
    app.logger.info('Starting kato agent')
    _recover_orphan_workspaces(app)
    _reconcile_workspace_branches(app)
    _resume_streaming_sessions(app)
    _start_planning_webserver_if_enabled(app)
    _register_shutdown_hook(app)
    startup_delay_seconds, scan_interval_seconds = _task_scan_settings(cfg)
    _run_task_scan_loop(
        app,
        startup_delay_seconds=startup_delay_seconds,
        scan_interval_seconds=scan_interval_seconds,
    )
    return 0


def _reconcile_workspace_branches(app) -> None:
    """Walk every workspace and align each clone to its task branch.

    Per-task workspace clones are *supposed* to live on the branch
    named after the task (kato's convention: ``branch_name == task_id``).
    A previous kato session can leave them on ``master`` if it crashed
    mid-publish, or if a manual recovery left the clone in a weird
    state. This runs once at boot, idempotent and best-effort —
    workspaces that are dirty / can't be cleanly checked out are
    skipped with a warning so kato still boots.
    """
    workspace_manager = getattr(app, 'workspace_manager', None)
    if workspace_manager is None:
        return
    try:
        from kato_webserver.git_diff_utils import (
            current_branch,
            ensure_branch_checked_out,
        )
    except ImportError:
        # Webserver not on the path — kato can run headless without it.
        return
    try:
        records = workspace_manager.list_workspaces()
    except Exception:
        app.logger.exception('failed to list workspaces during branch reconcile')
        return
    realigned = 0
    skipped: list[str] = []
    for record in records:
        task_id = str(getattr(record, 'task_id', '') or '')
        if not task_id:
            continue
        for repository_id in (getattr(record, 'repository_ids', []) or []):
            try:
                clone_path = workspace_manager.repository_path(
                    task_id, str(repository_id),
                )
            except Exception:
                continue
            if not clone_path.is_dir() or not (clone_path / '.git').is_dir():
                continue
            cwd = str(clone_path)
            on = current_branch(cwd)
            if on == task_id:
                continue
            if ensure_branch_checked_out(cwd, task_id):
                app.logger.info(
                    'workspace %s/%s realigned: %s -> %s',
                    task_id, repository_id, on or '<unknown>', task_id,
                )
                realigned += 1
            else:
                skipped.append(f'{task_id}/{repository_id} (on {on or "<unknown>"})')
    if realigned:
        app.logger.info(
            'realigned %d workspace clone(s) to their task branch',
            realigned,
        )
    if skipped:
        app.logger.warning(
            'could not realign %d workspace clone(s) to task branch '
            '(dirty tree or missing branch): %s',
            len(skipped), ', '.join(skipped[:10]),
        )


_RESUME_CONTINUE_PROMPT = (
    "kato has been restarted while this task workspace was still active. "
    "Resume the interrupted task now. First inspect the existing worktree "
    "and conversation context so you do not duplicate or overwrite work, "
    "then continue from the last safe point. If the task is already complete, "
    "say so and end with the normal Kato completion token."
)

_RESUME_WAIT_PROMPT = (
    "kato has been restarted. This is a system notice — no user "
    "action requested. Please reply with one short line "
    "acknowledging you're ready to continue, then wait for the "
    "operator's next message."
)


def _resume_streaming_sessions(app) -> None:
    """Re-spawn Claude sessions for every active workspace at boot.

    Without this, restarting kato leaves every previously-open chat
    tab in a "Claude: sleeping" state until the operator types into
    it. We walk the workspace registry and call ``start_session`` for
    each ``active`` workspace; the session manager's existing
    resume-id plumbing reuses the saved ``claude_session_id`` so the
    chat picks up where it left off. A short system-notice prompt is
    sent so the Claude CLI doesn't exit on empty stdin (it requires
    at least one message at startup).

    Best-effort: any per-task failure is logged and skipped — the tab
    falls back to the existing "operator types to wake it" path.
    """
    session_manager = getattr(app, 'session_manager', None)
    workspace_manager = getattr(app, 'workspace_manager', None)
    runner = getattr(app, 'planning_session_runner', None)
    if session_manager is None or workspace_manager is None:
        return
    try:
        from kato_core_lib.data_layers.service.workspace_manager import (
            WORKSPACE_STATUS_ACTIVE,
            WORKSPACE_STATUS_PROVISIONING,
        )
    except ImportError:
        return
    try:
        records = workspace_manager.list_workspaces()
    except Exception:
        app.logger.exception('failed to list workspaces during session resume')
        return
    spawn_defaults = _planning_spawn_defaults(runner)
    architecture_doc_path = (
        os.environ.get('KATO_ARCHITECTURE_DOC_PATH', '') or ''
    )
    resumed = 0
    skipped: list[str] = []
    for record in records:
        task_id = str(getattr(record, 'task_id', '') or '')
        if not task_id:
            continue
        status = str(getattr(record, 'status', '') or '')
        if status not in (WORKSPACE_STATUS_ACTIVE, WORKSPACE_STATUS_PROVISIONING):
            continue
        cwd = str(getattr(record, 'cwd', '') or '')
        if not cwd:
            # Fall back to the first repo clone if cwd wasn't recorded.
            for repo_id in (getattr(record, 'repository_ids', []) or []):
                try:
                    candidate = workspace_manager.repository_path(
                        task_id, str(repo_id),
                    )
                except Exception:
                    continue
                if candidate.is_dir():
                    cwd = str(candidate)
                    break
        if not cwd:
            skipped.append(f'{task_id} (no cwd)')
            continue
        try:
            initial_prompt = _resume_prompt_for_workspace(record)
            session_manager.start_session(
                task_id=task_id,
                task_summary=str(getattr(record, 'task_summary', '') or ''),
                initial_prompt=initial_prompt,
                cwd=cwd,
                expected_branch=task_id,
                architecture_doc_path=architecture_doc_path,
                **spawn_defaults,
            )
            resumed += 1
        except Exception as exc:
            app.logger.warning(
                'could not resume Claude session for %s: %s '
                '(operator can send a message to wake the tab manually)',
                task_id, exc,
            )
            skipped.append(task_id)
    if resumed:
        app.logger.info(
            'resumed %d Claude session(s) from previous kato run',
            resumed,
        )
    if skipped:
        app.logger.info(
            'skipped resume for %d session(s): %s',
            len(skipped), ', '.join(skipped[:10]),
        )


def _planning_spawn_defaults(runner) -> dict[str, object]:
    """Mirror ``WaitPlanningService._session_starter_defaults`` so the
    resumed sessions use the same binary / model / permission-mode the
    autonomous flow uses.
    """
    if runner is None:
        return {}
    defaults = getattr(runner, '_defaults', None)
    if defaults is None:
        return {}
    fields = (
        'binary',
        'model',
        'permission_mode',
        'permission_prompt_tool',
        'allowed_tools',
        'disallowed_tools',
        'effort',
    )
    result: dict[str, object] = {
        field: (getattr(defaults, field, '') or '') for field in fields
    }
    result['max_turns'] = getattr(defaults, 'max_turns', None)
    return result


def _resume_prompt_for_workspace(record) -> str:
    if bool(getattr(record, 'resume_on_startup', True)):
        return agent_prompt_utils.prepend_forbidden_repository_guardrails(
            _RESUME_CONTINUE_PROMPT,
        )
    return agent_prompt_utils.prepend_forbidden_repository_guardrails(_RESUME_WAIT_PROMPT)


def _recover_orphan_workspaces(app) -> None:
    """Adopt out-of-band task folders dropped under ``KATO_WORKSPACES_ROOT``.

    Best-effort, runs exactly once per kato process. Failures are logged
    and swallowed so a flaky filesystem can't block startup.
    """
    recovery = getattr(app, 'workspace_recovery_service', None)
    if recovery is None:
        return
    try:
        adopted = recovery.recover_orphan_workspaces()
    except Exception:
        app.logger.exception('workspace recovery failed; continuing without it')
        return
    if adopted:
        app.logger.info(
            'recovered %d orphan workspace%s during startup',
            len(adopted),
            '' if len(adopted) == 1 else 's',
        )


def _start_planning_webserver_if_enabled(app) -> None:
    """Boot the Flask planning UI in a daemon thread inside this process.

    We run kato + webserver in the same Python process so they share the
    in-memory :class:`ClaudeSessionManager`. The webserver lives in a
    separate package (``webserver/``) but is imported here so the live
    sessions the orchestrator creates are the same ones the browser tabs
    talk to.
    """
    import os
    import threading

    if str(os.environ.get('KATO_WEBSERVER_DISABLED', '')).strip().lower() in {'1', 'true', 'yes', 'on'}:
        app.logger.info('planning webserver disabled via KATO_WEBSERVER_DISABLED')
        return

    session_manager = getattr(app, 'session_manager', None)
    workspace_manager = getattr(app, 'workspace_manager', None)
    planning_session_runner = getattr(app, 'planning_session_runner', None)
    if session_manager is None and workspace_manager is None:
        # Both backends now use a workspace manager, so this only fires
        # in stripped-down setups (tests, embedded use). Nothing to
        # render → keep the webserver off.
        app.logger.info(
            'planning webserver skipped (no session_manager / workspace_manager wired)'
        )
        return

    try:
        from kato_webserver.app import create_app as _create_webserver_app
    except ImportError as exc:
        app.logger.warning(
            'planning webserver not available (%s); install ./webserver to enable', exc,
        )
        return

    host = os.environ.get('KATO_WEBSERVER_HOST', '127.0.0.1')
    port = int(os.environ.get('KATO_WEBSERVER_PORT', '5050'))
    flask_app = _create_webserver_app(
        session_manager=session_manager,
        workspace_manager=workspace_manager,
        planning_session_runner=planning_session_runner,
        status_broadcaster=_STATUS_BROADCASTER,
        agent_service=getattr(app, 'service', None),
    )

    # Silence Werkzeug's per-request access log — the planning UI polls
    # /api/sessions every 5s and that drowns the kato terminal in noise.
    # Errors and tracebacks still come through (they go to stderr).
    import logging as _logging
    _logging.getLogger('werkzeug').setLevel(_logging.ERROR)

    def _serve() -> None:
        try:
            flask_app.run(host=host, port=port, debug=False, use_reloader=False)
        except Exception:
            app.logger.exception('planning webserver crashed')

    thread = threading.Thread(
        target=_serve,
        name='kato-planning-webserver',
        daemon=True,
    )
    thread.start()
    url = f'http://{host}:{port}'
    app.logger.info('planning webserver listening on %s', url)
    _open_browser_when_ready(url, app.logger)


def _open_browser_when_ready(url: str, logger) -> None:
    """Wait until the planning webserver answers, then open the browser tab.

    Off by default in CI / headless setups: set ``KATO_OPEN_BROWSER=0`` to
    skip. The poll runs in a daemon thread so kato's main loop never waits
    on the browser opening.
    """
    import os
    import threading
    import time
    import urllib.error
    import urllib.request
    import webbrowser

    if str(os.environ.get('KATO_OPEN_BROWSER', '1')).strip().lower() in {'0', 'false', 'no', 'off'}:
        return

    def _wait_and_open() -> None:
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f'{url}/healthz', timeout=1):
                    break
            except (urllib.error.URLError, OSError):
                time.sleep(0.25)
        else:
            logger.warning('planning webserver never answered /healthz; not opening browser')
            return
        try:
            webbrowser.open_new_tab(url)
        except Exception:
            logger.exception('failed to open planning UI in browser')

    threading.Thread(
        target=_wait_and_open,
        name='kato-open-browser',
        daemon=True,
    ).start()


def _register_shutdown_hook(app) -> None:
    def _shutdown(signum, frame):
        app.logger.info('shutting down kato agent (signal %s)', signum)
        try:
            app.service.shutdown()
        except Exception:
            app.logger.exception('error during shutdown cleanup')
        raise SystemExit(0)

    # SIGINT works on every supported platform. SIGTERM works on POSIX
    # but Windows refuses to install a Python handler for it (and
    # delivers TerminateProcess instead) — register defensively so
    # kato boots cleanly on Windows shells too.
    signal.signal(signal.SIGINT, _shutdown)
    try:
        signal.signal(signal.SIGTERM, _shutdown)
    except (AttributeError, ValueError):
        app.logger.debug(
            'SIGTERM handler not installable on this platform; '
            'relying on SIGINT for graceful shutdown',
        )


def _task_scan_settings(cfg: DictConfig) -> tuple[float, float]:
    task_scan_cfg = cfg.kato.get('task_scan', {}) or {}
    return (
        float(task_scan_cfg.get('startup_delay_seconds', 30.0)),
        float(task_scan_cfg.get('scan_interval_seconds', 60.0)),
    )


def _run_task_scan_loop(
    app,
    *,
    startup_delay_seconds: float,
    scan_interval_seconds: float,
    sleep_fn=time.sleep,
    max_cycles: int | None = None,
) -> None:
    job = ProcessAssignedTasksJob()
    job.initialized(app)
    if startup_delay_seconds > 0:
        if supports_inline_status():
            sleep_with_warmup_countdown(
                startup_delay_seconds,
                sleep_fn=sleep_fn,
            )
        else:
            app.logger.info(
                'Waiting %s before scanning tasks while Kato warms up',
                _formatted_duration_text(startup_delay_seconds),
            )
            sleep_fn(startup_delay_seconds)

    cycles = 0
    while True:
        app.logger.info('Scanning for new tasks and reviews')
        try:
            job.run()
            app.logger.info('Scan complete')
        except Exception:
            app.logger.warning(
                'task scan failed; retrying in %s seconds',
                scan_interval_seconds,
            )

        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            return
        if scan_interval_seconds > 0:
            _idle_with_heartbeat(
                scan_interval_seconds,
                logger=app.logger,
                sleep_fn=sleep_fn,
            )


def _idle_with_heartbeat(
    interval_seconds: float,
    *,
    logger,
    sleep_fn=time.sleep,
    heartbeat_seconds: float = 5.0,
) -> None:
    """Sleep ``interval_seconds`` between scan ticks.

    The terminal sees a single inline-status line that updates in place
    via carriage-return; no new lines per heartbeat. The planning UI's
    SSE feed sees one heartbeat entry per ``heartbeat_seconds`` chunk so
    the status bar shows a live countdown — published directly to the
    broadcaster (bypassing the Python logger so it doesn't double-print
    to stderr).

    The loop is driven by chunk count, not wall-clock, so a mocked
    ``sleep_fn`` in tests doesn't have to also patch ``time.monotonic``.
    """
    del logger  # unused: we publish to the broadcaster directly now
    total = float(interval_seconds)
    if total <= 0:
        return
    step = max(1.0, float(heartbeat_seconds))
    use_spinner = supports_inline_status()
    remaining = total
    while remaining > 0:
        chunk = step if remaining >= step else remaining
        countdown = int(round(remaining))
        # Push the heartbeat to the SSE feed only — the broadcaster bypasses
        # the Python logger so no new line lands on the terminal.
        _STATUS_BROADCASTER.publish(
            level='INFO',
            logger_name='kato.heartbeat',
            message=f'Idle · next scan in {countdown}s',
        )
        if use_spinner:
            # Carriage-return spinner with countdown — single inline line
            # the terminal updates in place every chunk.
            sleep_with_countdown_spinner(
                chunk,
                status_text='Idle · next scan in',
                countdown_seconds=countdown,
                sleep_fn=sleep_fn,
            )
        else:
            sleep_fn(chunk)
        remaining -= chunk


def _formatted_duration_text(seconds: float) -> str:
    normalized_seconds = float(seconds)
    rounded_seconds = int(normalized_seconds)
    if normalized_seconds == rounded_seconds:
        seconds_label = 'second' if rounded_seconds == 1 else 'seconds'
        return f'{rounded_seconds} {seconds_label}'
    return f'{normalized_seconds:.1f} seconds'


if __name__ == '__main__':
    raise SystemExit(main())
