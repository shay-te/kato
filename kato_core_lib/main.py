from __future__ import annotations

import hashlib
import os
import signal
import sys
import threading
import time

import hydra
from omegaconf import DictConfig

from agent_core_lib.agent_core_lib.helpers import agent_prompt_utils
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.shell_status_utils import (
    sleep_with_countdown_spinner,
    supports_inline_status,
)
from kato_core_lib.helpers.status_broadcaster_utils import (
    StatusBroadcaster,
    install_status_broadcast_handler,
)
from sandbox_core_lib.sandbox_core_lib.tls_pin import (
    TlsPinError,
    validate_anthropic_tls_pin_or_refuse,
)
from agent_core_lib.agent_core_lib.helpers.session_id_utils import (
    read_session_id_from,
)
from kato_core_lib.helpers.action_guard_config import print_action_guard_posture
from kato_core_lib.errors import AgentBackendChangedError
from kato_core_lib.validate_env import collect_config_errors, collect_runtime_errors
from sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator import (
    BypassPermissionsRefused,
    print_security_posture,
    validate_bypass_permissions,
    validate_read_only_tools_requires_docker,
)


_STATUS_BROADCASTER = StatusBroadcaster()
install_status_broadcast_handler(_STATUS_BROADCASTER)

# Shared between the scan loop thread and the Flask webserver thread.
# _FORCE_SCAN_EVENT: set by POST /api/scan/trigger to wake the idle loop early.
# _SCAN_IN_PROGRESS: set while the scan job is running so the endpoint can
# report the current state without doing anything.
_FORCE_SCAN_EVENT: threading.Event = threading.Event()
_SCAN_IN_PROGRESS: threading.Event = threading.Event()


class _ProcessAssignedTasksJobProxy:
    def __call__(self):
        from kato_core_lib.jobs.process_assigned_tasks import ProcessAssignedTasksJob as _ProcessAssignedTasksJob

        return _ProcessAssignedTasksJob()


class _KatoInstanceProxy:
    @staticmethod
    def init(
        core_lib_cfg: DictConfig,
        setup_mode: bool = False,
        defer_validation: bool = False,
    ) -> None:
        from kato_core_lib.kato_instance import KatoInstance as _KatoInstance

        _KatoInstance.init(
            core_lib_cfg,
            setup_mode=setup_mode,
            defer_validation=defer_validation,
        )

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
    # Missing config no longer hard-exits at the terminal: kato boots into
    # SETUP MODE (the planning UI runs so the operator configures via the
    # Settings drawer) instead of dying. The SECURITY gates below (bypass /
    # docker / TLS) STILL exit — those are safety, not fill-in-later config.
    config_errors = collect_config_errors(mode='all')
    setup_mode = bool(config_errors)
    if setup_mode:
        # One line only — the setup WIZARD in the browser shows the full
        # detail; the terminal just needs to say why kato isn't scanning.
        logger.warning(
            'kato is not configured yet (%d setting(s) missing) — opening '
            'the setup wizard in the browser.', len(config_errors),
        )
    # Runtime problems are NOT setup problems. An uninstalled agent CLI used
    # to land in ``config_errors`` and therefore trip SETUP MODE, so a fully
    # configured install greeted the operator with the first-run wizard and
    # asked them to re-enter settings that were already right. The fix is on
    # the host, not in the Settings UI, so it is logged here and shown by the
    # agent-version banner instead of gating the boot.
    for runtime_error in collect_runtime_errors():
        logger.warning('%s', runtime_error)
    try:
        validate_bypass_permissions()
    except BypassPermissionsRefused as exc:
        logger.error('%s', exc)
        return 1
    # Read-only-tools pre-approval requires the sandbox boundary.
    # ``KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS=true`` without
    # ``KATO_CLAUDE_DOCKER=true`` would let pre-approved ``grep``/
    # ``cat``/``find`` run on the host with the operator's full
    # file-system access — defeating the purpose of pre-approving
    # only "safe" tools. Refused at startup the same way bypass
    # without docker is.
    try:
        validate_read_only_tools_requires_docker()
    except BypassPermissionsRefused as exc:
        logger.error('%s', exc)
        return 1
    # OG4 — TLS pin validator for api.anthropic.com. Strict-by-default
    # when ``KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256`` is set; opt-out
    # via ``KATO_SANDBOX_ALLOW_NO_TLS_PIN=true``. A network failure at
    # boot does NOT promote to refusal (operator may be offline /
    # in a build env without connectivity); only a successful
    # connection with a pin mismatch fails-closed. See
    # ``sandbox_core_lib.sandbox_core_lib.tls_pin``.
    try:
        validate_anthropic_tls_pin_or_refuse(logger=logger)
    except TlsPinError as exc:
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
    from sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator import is_docker_mode_enabled
    if is_docker_mode_enabled():
        from sandbox_core_lib.sandbox_core_lib.manager import (
            check_docker_or_exit,
            check_gvisor_or_exit,
            docker_running_rootless,
            gvisor_runtime_available,
            install_host_egress_backstop,
            prune_stale_secret_dirs,
            verify_audit_chain,
            reap_orphan_sandbox_containers,
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
        # Sweep sandbox containers left behind by a PREVIOUS kato that
        # died without unwinding (SIGKILL, power loss, closed terminal).
        # ``docker run --rm`` only fires when the container's own process
        # exits, so those keep running — workspace bind-mounted, creds in
        # tmpfs — until something removes them. Containers owned by a
        # LIVE process are left alone, so a second kato on the same host
        # is unaffected. Best-effort: never blocks startup.
        try:
            reaped = reap_orphan_sandbox_containers(logger=logger)
            if reaped:
                logger.warning(
                    'sandbox: reaped %d orphaned container(s) from a '
                    'previous run', len(reaped),
                )
            # Same sweep for the out-of-band secret drops: a drop whose
            # container is gone is a stray file holding an API key.
            dropped = prune_stale_secret_dirs()
            if dropped:
                logger.info(
                    'sandbox: pruned %d stale secret drop(s)', len(dropped),
                )
            # Host-side egress floor for the sandbox bridge. The
            # in-container firewall is the precise control; this one sits
            # where the container cannot reach it, so flushing the inner
            # rules still leaves only TCP/443 + pinned DNS. Best-effort:
            # the primary control applies regardless.
            install_host_egress_backstop(logger=logger)
            # The spawn log is hash-chained for tamper-evidence, which is
            # only worth anything if something reads it. Read-only, never
            # raises; a broken chain logs an error rather than blocking.
            verify_audit_chain(logger=logger)
        except Exception:
            logger.warning(
                'sandbox: orphan container sweep failed; continuing boot',
                exc_info=True,
            )
    print_security_posture()
    print_action_guard_posture()
    try:
        # ``defer_validation=True``: build the agent service but DON'T run the
        # network connection checks inside __init__. On a configured boot those
        # checks (providers + agent CLI) used to sit on the critical path
        # BEFORE the planning webserver bound — the desktop splash waited the
        # full ~minute for them. We now serve the UI first and validate in a
        # background thread (see ``_finalize_configured_boot``). Setup-mode is
        # unaffected (it never validates in __init__; complete_setup does).
        KatoInstance.init(cfg, setup_mode=setup_mode, defer_validation=True)
    except RuntimeError as exc:
        if str(exc).startswith('startup dependency validation failed:') or str(exc).startswith('[Error] '):
            logger.error('%s', exc)
            return 1
        raise
    app = KatoInstance.get()
    app.logger = getattr(app, 'logger', None) or logger
    app.logger.info('Starting kato agent')
    if setup_mode:
        # Not configured: run ONLY the webserver so the operator can configure
        # in the UI. Skip the scan loop + task-oriented boot steps (no ticket
        # service yet). The wait loop watches the layered config and finishes
        # the boot IN-PROCESS the moment configuration completes — the
        # operator never touches a terminal.
        _start_planning_webserver_if_enabled(app)
        _register_shutdown_hook(app)
        app.logger.warning(
            'kato is running in SETUP MODE — configure it in the planning UI '
            'and kato will start on its own.',
        )
        if not _wait_until_configured_then_finish_setup(app):
            return 0
        # Fall through: the agent service is live AND already validated
        # (complete_setup ran validate_connections). The webserver has been
        # serving the whole time, so we finish the boot synchronously here —
        # there is no UI to keep waiting on. The reconciliation steps must run
        # BEFORE the UI leaves setup mode so a restart never renders a
        # half-reconciled workspace list.
        _load_hooks_or_refuse(app, logger)
        _run_boot_reconciliation(app)
        _start_planning_webserver_if_enabled(app)  # no-op, already serving
        _mark_webserver_configured(app)
        _start_post_boot_workers(app)
        _register_shutdown_hook(app)
        scan_interval_seconds = _task_scan_settings(cfg)
        _run_task_scan_loop(
            app,
            scan_interval_seconds=scan_interval_seconds,
            force_scan_event=_FORCE_SCAN_EVENT,
        )
        return 0
    # --- Configured-at-boot: UI-FIRST fast path ---------------------------
    # Hooks are a local, fail-closed config gate (bad hook schema must abort
    # the boot loudly), so they load BEFORE we serve. Everything network-bound
    # (connection validation, done-task cleanup) and every disk-walk
    # reconciliation moves into a background thread so the planning webserver
    # binds in ~seconds instead of after the ~minute validation tail.
    _load_hooks_or_refuse(app, logger)
    _start_planning_webserver_if_enabled(app)
    _register_shutdown_hook(app)
    boot_ready_event = threading.Event()
    threading.Thread(
        target=lambda: _finalize_configured_boot(app, boot_ready_event),
        name='kato-boot-finalize',
        daemon=True,
    ).start()
    scan_interval_seconds = _task_scan_settings(cfg)
    # The scan loop waits on ``boot_ready_event`` before its first tick, so no
    # autonomous scan runs until connections are validated and reconciliation
    # is done. If validation never succeeds the event never fires: the main
    # thread parks there, the webserver keeps serving, and the UI shows the
    # validation error (see ``_finalize_configured_boot``) — the UI-first
    # replacement for the old fail-closed process exit.
    _run_task_scan_loop(
        app,
        scan_interval_seconds=scan_interval_seconds,
        force_scan_event=_FORCE_SCAN_EVENT,
        ready_event=boot_ready_event,
    )
    return 0


def _run_boot_reconciliation(app) -> None:
    """The best-effort, idempotent boot-time reconciliation steps.

    Grouped so both the setup→running fall-through (runs them synchronously,
    the UI is already up) and the configured UI-first boot (runs them in the
    background finalize thread) share one ordering. Every step is
    self-correcting on the next scan tick, so a background run only means a
    freshly-restarted UI may briefly show a not-yet-reconciled workspace/tab
    for the few seconds before this completes.

    Sessions stay lazy after restart: opening a tab replays the disk JSONL via
    SSE so history shows immediately; Claude is re-spawned (``--resume``) only
    when the operator sends a follow-up — same UX as VS Code Claude Code.
    """
    _recover_orphan_workspaces(app)
    _reconcile_workspace_branches(app)
    _reset_stuck_workspace_statuses(app)
    _requeue_stuck_comments(app)
    _log_known_session_ids(app)
    _cleanup_done_tasks_at_boot(app)


def _start_post_boot_workers(app) -> None:
    """Start the always-on background workers + inventory warm-up.

    These need the agent service to exist but not to be reachable, and none
    block the boot: queued-comment dispatch waits for the UI healthz itself,
    the two watchers are best-effort, and the inventory warm-up runs off-thread.
    """
    _start_pending_comment_work_after_ui(app)
    _start_resume_prompt_watcher(app)
    _start_comment_run_watcher(app)
    _warm_up_repository_inventory(app)


# How long the configured UI-first boot waits between failed connection-
# validation attempts. The webserver is already serving and showing the error
# the whole time; a shorter interval just re-hits the providers more often.
_CONFIGURED_BOOT_VALIDATION_RETRY_SECONDS = 30.0


def _finalize_configured_boot(
    app,
    ready_event: threading.Event,
    *,
    sleep_fn=time.sleep,
    max_attempts: int | None = None,
) -> None:
    """Validate connections + reconcile in the background, then release the
    scan loop.

    Runs off the main thread on a configured boot so the planning webserver is
    already serving. Connection validation is retried (the UI shows the error
    banner between attempts) instead of exiting the process — the UI-first
    replacement for the old fail-closed boot. Once validation passes, the
    reconciliation steps run, the webserver is flipped fully live, the
    background workers start, and ``ready_event`` is set so the scan loop's
    first tick fires.

    ``max_attempts`` is a test escape hatch; production passes ``None`` (retry
    until the providers come back or the operator fixes Settings).
    """
    attempt = 0
    while max_attempts is None or attempt < max_attempts:
        attempt += 1
        try:
            app.service.validate_connections()
        except Exception as exc:
            # Surface ANY validation failure and keep serving — the whole point
            # of the UI-first boot is that a bad token / unreachable provider
            # shows in the UI instead of killing the process.
            message = str(exc)
            app.logger.error(
                'connection validation failed; the planning UI is up and '
                'showing the error — retrying in %ss: %s',
                _CONFIGURED_BOOT_VALIDATION_RETRY_SECONDS, message,
            )
            _flag_boot_validation_error(app, message[:500])
            try:
                sleep_fn(_CONFIGURED_BOOT_VALIDATION_RETRY_SECONDS)
            except (KeyboardInterrupt, SystemExit):
                return
            continue
        break
    else:
        return
    _run_boot_reconciliation(app)
    # Clears the error banner + NEEDS_CONFIG from any failed attempt above and
    # publishes the live service to the webserver config.
    _mark_webserver_configured(app)
    _start_post_boot_workers(app)
    ready_event.set()
    app.logger.info('kato is fully started — autonomous scanning enabled')


def _flag_boot_validation_error(app, message: str) -> None:
    """Surface a configured-boot validation failure in the planning UI.

    Publishes the error and flips the LIVE Flask app into the "needs config"
    surface so the onboarding/error banner renders it — a configured boot
    starts with ``NEEDS_CONFIG=False``, so without this the wizard that shows
    ``setup_error`` would never appear. ``_mark_webserver_configured`` clears
    both again once validation finally succeeds.
    """
    flask_app = getattr(app, 'planning_flask_app', None)
    if flask_app is None:
        return
    flask_app.config['SETUP_ERROR'] = message
    flask_app.config['NEEDS_CONFIG'] = True


# How often the setup-mode wait loop re-checks the layered config. Short
# enough that "save in the wizard → kato starts" feels immediate; the check
# is a local settings.json read, no network.
_SETUP_POLL_SECONDS = 5.0
# A failed start attempt is normally re-tried only when the config CHANGES
# (fingerprint guard, so a bad token never hammers the provider every tick).
# But a transient failure (provider blip) or a value shadowed by the spawn
# env would then never retry — so an unchanged config is re-attempted every
# this-many ticks anyway (~2 minutes at the 5s poll).
_SETUP_RETRY_EVERY_TICKS = 24


def _config_env_fingerprint(env: dict) -> str:
    """Stable digest of a config env, used to avoid re-attempting startup
    (which runs real connection validation against the providers) until the
    operator actually changes something in Settings."""
    payload = repr(sorted(env.items()))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _wait_until_configured_then_finish_setup(
    app,
    sleep_fn=time.sleep,
    max_ticks: int | None = None,
) -> bool:
    """SETUP MODE park loop: poll the layered config until it is complete,
    then finish the boot in-process.

    Each tick re-evaluates ``collect_config_errors`` over the SAME layered
    env the ``/api/config-status`` endpoint uses (``effective_config_env``:
    process env > settings.json), so the UI's "you're all set" and
    this loop can never disagree. Once complete, the newly-saved values are
    loaded into the process env (shell still wins — hydra's
    ``${oc.env:...}`` reads resolve on access) and
    ``complete_setup()`` builds the real agent service.

    Returns ``True`` when setup finished and the full boot should continue;
    ``False`` on shutdown (Ctrl-C / SIGTERM) or when ``max_ticks`` — a test
    escape hatch — runs out. A failed start attempt (e.g. bad credentials
    failing connection validation) keeps kato in setup mode and is NOT
    retried until the config changes — plus a slow periodic re-attempt
    (``_SETUP_RETRY_EVERY_TICKS``) so a transient provider blip or a value
    shadowed by the spawn-time env can still recover without a restart.
    """
    from kato_core_lib.helpers.kato_settings_store_utils import (
        effective_config_env,
    )

    last_failed_fingerprint = ''
    ticks = 0
    ticks_since_attempt = 0
    while max_ticks is None or ticks < max_ticks:
        ticks += 1
        ticks_since_attempt += 1
        try:
            sleep_fn(_SETUP_POLL_SECONDS)
        except (KeyboardInterrupt, SystemExit):
            return False
        env = effective_config_env()
        if collect_config_errors(mode='all', env=env):
            continue
        fingerprint = _config_env_fingerprint(env)
        if (
            fingerprint == last_failed_fingerprint
            and ticks_since_attempt < _SETUP_RETRY_EVERY_TICKS
        ):
            continue
        ticks_since_attempt = 0
        # Snapshot so a refused/failed attempt restores the env EXACTLY —
        # including keys complete_setup itself exports. Leaving residue
        # would shadow a corrected value saved later in the UI (process env
        # outranks settings.json), wedging setup mode until a restart.
        env_snapshot = dict(os.environ)
        _load_layered_config_into_environ()
        # SECURITY: the boot-time gates validated the env as it was at
        # startup. The operator may have saved gate-relevant flags DURING
        # setup (KATO_CLAUDE_BYPASS_PERMISSIONS / read-only pre-approval /
        # TLS pin / docker mode), so the same hard gates must pass against
        # the values just loaded before anything starts. Refusal keeps kato
        # in setup mode (strictly safer than exiting — nothing runs either
        # way, and the operator sees why in the wizard).
        gate_refusal = _run_transition_security_gates(app)
        if gate_refusal:
            _restore_environ(env_snapshot)
            last_failed_fingerprint = fingerprint
            app.logger.error('%s', gate_refusal)
            _set_webserver_setup_error(app, gate_refusal[:500])
            continue
        try:
            app.complete_setup()
        except AgentBackendChangedError as exc:
            # The setup boot built its managers for the OLD backend and the
            # webserver holds references to them — the switch can't be
            # applied live. Deliver the no-terminal promise anyway: re-exec
            # kato in place. The just-loaded env (new backend included)
            # survives the exec, so the new process boots CONFIGURED and
            # the browser gate closes on its next poll.
            app.logger.warning('%s', exc)
            _set_webserver_setup_error(app, (
                'kato is restarting to apply the new agent backend — '
                'this page reconnects by itself in a few seconds.'
            ))
            try:
                _restart_in_place(app)
            except OSError:
                app.logger.exception(
                    'in-place restart failed — restart kato manually to '
                    'apply the new agent backend',
                )
                _restore_environ(env_snapshot)
                last_failed_fingerprint = fingerprint
            continue
        except Exception as exc:
            _restore_environ(env_snapshot)
            last_failed_fingerprint = fingerprint
            app.logger.exception(
                'configuration looks complete but starting kato failed — '
                'still in setup mode; fix the values in Settings and kato '
                'will retry automatically',
            )
            # Surface the failure to the UI too: without this the wizard
            # would keep saying "you're all set" while the terminal alone
            # knows the start failed. Validation messages name hosts /
            # projects, never secret values.
            _set_webserver_setup_error(app, (
                f'{str(exc)[:500]} — kato retries automatically after you '
                'save a change (and periodically). If the failing value was '
                'already set when kato started (shell env), restart '
                'kato to apply the new one.'
            ))
            continue
        # Clear any earlier failure immediately, but DON'T flip the UI out
        # of setup mode yet: main() first runs the same boot reconciliation
        # a configured boot runs BEFORE serving (orphan recovery, branch
        # reconcile, comment requeue). Flipping now would let the operator
        # race those steps — chat sends against workspaces whose branches
        # are still being checked out. ``_mark_webserver_configured`` runs
        # in the fall-through boot at the configured-boot equivalence point.
        _set_webserver_setup_error(app, '')
        app.logger.info('configuration complete — kato is starting')
        return True
    return False


# Exit-code contract with scripts/run_local.py: exiting with this code asks
# the launcher to relaunch kato cleanly (full teardown → port free).
_RESTART_EXIT_CODE = 87


def _restart_in_place(app) -> None:
    """Restart the kato process. Never returns on success.

    Preferred path: the launcher (``kato up`` → run_local) supervises us —
    exit with the restart code and let it respawn a FRESH process. That is
    reliable on every platform: the old process is fully gone (webserver
    port released) before the new one starts.

    Fallback (direct ``python -m kato_core_lib.main`` runs, POSIX): re-exec
    the process image in place.
    """
    os.environ.pop('WERKZEUG_SERVER_FD', None)
    if os.environ.get('KATO_SUPERVISED_RESTART') == '1':
        app.logger.warning('restarting kato (supervised relaunch)…')
        os._exit(_RESTART_EXIT_CODE)
        return  # unreachable in production; keeps mocked-_exit tests honest
    argv = [sys.executable, '-m', 'kato_core_lib.main', *sys.argv[1:]]
    app.logger.warning('restarting kato in place: %s', ' '.join(argv))
    os.execv(sys.executable, argv)


def _restore_environ(snapshot: dict) -> None:
    """Restore ``os.environ`` to ``snapshot`` exactly — removes keys added
    since, reinstates removed ones, and reverts changed values."""
    for key in list(os.environ):
        if key not in snapshot:
            del os.environ[key]
    for key, value in snapshot.items():
        if os.environ.get(key) != value:
            os.environ[key] = value


def _run_transition_security_gates(app) -> str:
    """Re-run the boot security gates against the just-loaded env.

    Same validators the configured boot runs fatally (bypass double-confirm,
    read-only-tools-requires-docker, TLS pin, docker/gVisor preflight) — a
    flag saved through the Settings UI during setup mode must clear the
    exact same bar it would at a terminal boot. Returns ``''`` when every
    gate passes, else the refusal text for the wizard. The ``*_or_exit``
    docker preflights ``sys.exit`` by design; here that refusal is converted
    to "stay in setup mode", which runs nothing either way but keeps the
    setup UI alive so the operator can fix it.
    """
    try:
        validate_bypass_permissions()
        validate_read_only_tools_requires_docker()
        validate_anthropic_tls_pin_or_refuse(logger=app.logger)
    except (BypassPermissionsRefused, TlsPinError) as exc:
        return str(exc)
    from sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator import (
        is_docker_mode_enabled,
    )
    if is_docker_mode_enabled():
        from sandbox_core_lib.sandbox_core_lib.manager import (
            check_docker_or_exit,
            check_gvisor_or_exit,
        )
        try:
            check_docker_or_exit()
            check_gvisor_or_exit()
        except SystemExit:
            return (
                'docker sandbox preflight refused the saved configuration '
                '(KATO_CLAUDE_DOCKER is on but the Docker/gVisor requirements '
                'are not met — details in the kato terminal). Fix the values '
                'in Settings or start Docker, and kato will retry.'
            )
    return ''


def _set_webserver_setup_error(app, message: str) -> None:
    """Publish (or clear, with ``''``) the last failed start attempt's error
    on the LIVE Flask app so ``/api/config-status`` can show it in the setup
    wizard."""
    flask_app = getattr(app, 'planning_flask_app', None)
    if flask_app is None:
        return
    flask_app.config['SETUP_ERROR'] = message


def _load_layered_config_into_environ() -> None:
    """Load settings.json into the process env (shell wins).

    One deliberate difference from ``load_kato_settings_into_environ``:
    a key that is set-but-EMPTY in the process env is treated as unset
    and gets overwritten. Everything else (the validators, the settings
    UI, ``effective_config_env``) already treats empty as unset, so a
    blank env var inherited at spawn must not shadow the value the
    operator saves in the wizard forever. Callers snapshot/restore
    ``os.environ`` around failed attempts, so no rollback list is needed.
    """
    from kato_core_lib.helpers.kato_settings_store_utils import (
        read_kato_settings,
    )

    for key, value in read_kato_settings().items():
        if value and not os.environ.get(key):
            os.environ[key] = value


def _mark_webserver_configured(app) -> None:
    """Flip the LIVE Flask app out of setup mode after ``complete_setup``.

    The webserver thread started before the agent service existed, so its
    config carries ``AGENT_SERVICE=None`` + ``NEEDS_CONFIG=True``. Flask's
    config is a plain dict — updating it here is what makes
    ``/api/config-status`` report ``setup_mode: false`` (the UI's setup gate
    closes itself) and gives every service-backed route the live agent
    service, all without a restart.
    """
    flask_app = getattr(app, 'planning_flask_app', None)
    if flask_app is None:
        return
    flask_app.config['AGENT_SERVICE'] = getattr(app, 'service', None)
    flask_app.config['NEEDS_CONFIG'] = False
    # A stale error from an earlier failed attempt must not outlive success.
    flask_app.config['SETUP_ERROR'] = ''
    # The agent-CLI probe is cached for the process lifetime, and in setup
    # mode it ran BEFORE the operator supplied the backend settings — so it
    # cached "claude not found on PATH". Leaving it would greet a freshly
    # configured instance with a false "CLI not found" banner (and no Upgrade
    # button, since the plan collapses when the binary can't be resolved)
    # until someone happened to hit Refresh. Setup completion changes exactly
    # the inputs that probe reads, so drop it and let the next GET re-probe.
    flask_app.config.pop('AGENT_VERSION_INFO', None)


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


def _cleanup_done_tasks_at_boot(app) -> None:
    """Prune tabs for tickets already done/closed — once, at startup.

    Cleanup otherwise only runs on a scan tick (inside
    ``get_new_pull_request_comments``), so a restart would render a
    tab for any task whose ``~/.kato/sessions/<id>.json`` is still on
    disk even though the ticket moved to done — it'd only vanish
    ~30s later on the first scan. Running the prune here, BEFORE the
    planning webserver starts serving the tab list, means a restart
    never resurrects a done task even briefly. Connections were
    already validated at boot, so the ticket-platform call this
    needs is safe. Best-effort: any failure is logged and boot
    continues (the scan-loop cleanup is still the backstop).
    """
    service = getattr(app, 'service', None)
    cleanup = getattr(service, 'cleanup_done_tasks', None)
    if not callable(cleanup):
        return
    try:
        cleanup()
    except Exception:
        app.logger.exception(
            'boot-time done-task cleanup failed; the scan loop will '
            'retry it on the next tick',
        )


def _reset_stuck_workspace_statuses(app) -> None:
    """Promote PROVISIONING workspaces that have valid git repos to ACTIVE.

    A kato crash during provisioning leaves the workspace status stuck at
    PROVISIONING even though all or some repos may already be cloned. The
    on-demand chat-respawn path needs an ACTIVE workspace to attach to;
    promoting the ones that have at least one valid .git clone gives them
    a correct ACTIVE label so the lazy session spawn (on the operator's
    first follow-up message) lands in a workspace that's actually ready.

    Workspaces in ERRORED state are flagged with a visible warning so the
    operator knows they need attention (re-run the task or discard the
    workspace). Best-effort: any per-workspace failure is logged and skipped.
    """
    workspace_manager = getattr(app, 'workspace_manager', None)
    if workspace_manager is None:
        return
    try:
        from kato_core_lib.data_layers.service.workspace_manager import (
            WORKSPACE_STATUS_ACTIVE,
            WORKSPACE_STATUS_ERRORED,
            WORKSPACE_STATUS_PROVISIONING,
        )
    except ImportError:
        return
    try:
        records = workspace_manager.list_workspaces()
    except Exception:
        app.logger.exception('failed to list workspaces during status reset')
        return
    promoted = 0
    for record in records:
        task_id = str(getattr(record, 'task_id', '') or '')
        if not task_id:
            continue
        status = str(getattr(record, 'status', '') or '')
        if status == WORKSPACE_STATUS_ERRORED:
            app.logger.warning(
                'workspace %s is in errored state from a previous run — '
                'operator may need to re-run the task or discard the workspace',
                task_id,
            )
            continue
        if status != WORKSPACE_STATUS_PROVISIONING:
            continue
        has_valid_repo = _provisioning_workspace_has_git_repo(
            workspace_manager, task_id, record,
        )
        if has_valid_repo:
            try:
                workspace_manager.update_status(task_id, WORKSPACE_STATUS_ACTIVE)
                app.logger.info(
                    'workspace %s promoted from provisioning to active '
                    '(repos were cloned before the previous kato process stopped)',
                    task_id,
                )
                promoted += 1
            except Exception:
                app.logger.exception(
                    'failed to promote workspace %s from provisioning to active',
                    task_id,
                )
        else:
            app.logger.warning(
                'workspace %s is stuck in provisioning state with no valid '
                'git repos — the previous clone was incomplete. '
                'Re-run the task to provision it correctly.',
                task_id,
            )
    if promoted:
        app.logger.info(
            'promoted %d workspace(s) from provisioning to active at boot',
            promoted,
        )


def _provisioning_workspace_has_git_repo(
    workspace_manager, task_id: str, record,
) -> bool:
    """True when at least one repo clone under the workspace has a ``.git`` dir."""
    for repo_id in (getattr(record, 'repository_ids', []) or []):
        try:
            repo_path = workspace_manager.repository_path(task_id, str(repo_id))
        except Exception:
            continue
        if repo_path.is_dir() and (repo_path / '.git').exists():
            return True
    return False


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
    resume-id plumbing reuses the saved ``agent_session_id`` so the
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
    attach = getattr(session_manager, 'attach_workspace_manager', None)
    if callable(attach):
        attach(workspace_manager)
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


def _load_hooks_or_refuse(app, logger) -> None:
    """Ensure ``app.hook_runner`` is installed; load on demand if not.

    In the normal boot path, :class:`KatoCoreLib.__init__` already
    loaded hooks (so sub-services see a real runner at construction
    time) and this call is just a presence check. In test setups
    that bypass ``KatoInstance.init`` (mocking it out and supplying
    a SimpleNamespace ``app``), the loader runs here for the first
    time. Either way we end with ``app.hook_runner`` populated.

    Refuses startup on schema errors so config bugs surface at boot.
    """
    if getattr(app, 'hook_runner', None) is not None:
        return
    from kato_core_lib.hooks.config import HookConfigError, load_hooks_config
    from kato_core_lib.hooks.runner import HookRunner
    try:
        config = load_hooks_config()
    except HookConfigError as exc:
        logger.error('hooks config rejected: %s', exc)
        raise SystemExit(1) from exc
    app.hooks_config = config
    app.hook_runner = HookRunner(config, logger=logger)
    if not config.is_empty():
        points = sorted(p.value for p, hs in config.hooks_by_point.items() if hs)
        logger.info(
            'kato hooks loaded: %d point(s) configured (%s)',
            len(points), ', '.join(points),
        )


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

    if getattr(app, 'planning_flask_app', None) is not None:
        # Already serving — a setup-mode boot started the webserver before
        # the full boot fell through. Reuse the live thread; a second start
        # would only fail with "address already in use".
        return

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
    if host not in ('127.0.0.1', 'localhost', '::1'):
        # Every security control in this webserver (the CSRF guard's "no
        # Origin/Referer = trusted local caller" exemption, the complete
        # absence of a login) assumes the socket is reachable ONLY from
        # this machine. Binding to a non-loopback address (0.0.0.0, a LAN
        # IP, ...) hands every route -- including ones that return git
        # provider / task provider API tokens verbatim, run agent prompts,
        # and approve/deny live tool-permission asks -- to anyone who can
        # reach this host on the network, with zero authentication.
        app.logger.warning(
            'planning webserver is binding to %s, not loopback -- this '
            'exposes kato\'s ENTIRE unauthenticated API (including stored '
            'provider credentials) to anything that can reach this host on '
            'the network, not just this machine. Only do this if you '
            'understand and accept that risk (e.g. behind your own '
            'authenticating reverse proxy).',
            host,
        )
    scheme, ssl_context = _resolve_webserver_tls(app.logger)
    flask_app = _create_webserver_app(
        session_manager=session_manager,
        workspace_manager=workspace_manager,
        planning_session_runner=planning_session_runner,
        status_broadcaster=_STATUS_BROADCASTER,
        agent_service=getattr(app, 'service', None),
        force_scan_event=_FORCE_SCAN_EVENT,
        scan_in_progress_event=_SCAN_IN_PROGRESS,
        hook_runner=getattr(app, 'hook_runner', None),
        needs_config=getattr(app, 'needs_config', False),
    )

    # Keep a handle to the live Flask app: the setup-mode transition
    # (``_mark_webserver_configured``) mutates its config in place once the
    # agent service exists, instead of restarting the webserver.
    app.planning_flask_app = flask_app

    # Silence Werkzeug's per-request access log — the planning UI polls
    # /api/sessions every 5s and that drowns the kato terminal in noise.
    # Errors and tracebacks still come through (they go to stderr).
    import logging as _logging
    _logging.getLogger('werkzeug').setLevel(_logging.ERROR)

    def _serve() -> None:
        _serve_flask_with_bind_retry(
            flask_app, host, port, app.logger, ssl_context=ssl_context,
        )

    thread = threading.Thread(
        target=_serve,
        name='kato-planning-webserver',
        daemon=True,
    )
    thread.start()
    url = f'{scheme}://{host}:{port}'
    app.planning_webserver_url = url
    app.logger.info('planning webserver listening on %s', url)
    _open_browser_when_ready(url, app.logger)


def _resolve_webserver_tls(logger) -> tuple[str, tuple[str, str] | None]:
    """``(scheme, ssl_context)`` for the planning webserver.

    Enabled by default (``KATO_WEBSERVER_HTTPS=0`` opts out) — plain
    HTTP means anyone whose browser can reach the bound port (a page
    open on the operator's machine using it as a pivot, or the OS-level
    bind host if ever widened beyond loopback) can read/write everything
    kato serves, unencrypted. A loopback address can't get a CA-signed
    cert, so this generates + persists a self-signed one instead — the
    browser needs a one-time "trust this cert" click, not a restart-time
    one. Falls back to plain HTTP (never blocks startup) if the
    ``cryptography`` package is missing or the cert can't be written.
    """
    if str(os.environ.get('KATO_WEBSERVER_HTTPS', '1')).strip().lower() in {
        '0', 'false', 'no', 'off',
    }:
        return 'http', None
    from kato_core_lib.helpers.tls_cert_utils import ensure_local_tls_cert
    ssl_context = ensure_local_tls_cert(logger=logger, install_trust=True)
    return ('https', ssl_context) if ssl_context else ('http', None)


def _serve_flask_with_bind_retry(
    flask_app, host, port, logger,
    sleep_fn=time.sleep, attempts: int = 20, ssl_context=None,
) -> None:
    """Run the Flask dev server, retrying briefly when the port is busy.

    After a kato self-restart the OS may not have released the previous
    process's listen socket yet (particularly on Windows, where process
    replacement is emulated). Werkzeug reacts to a busy port with
    ``SystemExit`` — a BaseException a plain ``except Exception`` misses —
    so both are caught and retried for ~10s before giving up loudly.

    ``load_dotenv=False``: with python-dotenv installed, Flask's ``run()``
    otherwise auto-loads ``<cwd>/.env`` into ``os.environ`` — a hidden
    config path. kato reads ONLY ``~/.kato/settings.json`` (+ shell env).

    ``ssl_context`` is the ``(cert_path, key_path)`` pair from
    ``tls_cert_utils.ensure_local_tls_cert`` — ``None`` serves plain HTTP
    (HTTPS disabled or cert generation failed; see ``_resolve_webserver_tls``).
    """
    for attempt in range(attempts):
        try:
            flask_app.run(
                host=host, port=port, debug=False, use_reloader=False,
                threaded=True, load_dotenv=False, ssl_context=ssl_context,
            )
            return
        except (OSError, SystemExit):
            if attempt == attempts - 1:
                logger.error(
                    'planning webserver could not bind %s:%s after %d '
                    'attempts — is another kato still running?',
                    host, port, attempts,
                )
                return
            sleep_fn(0.5)
        except Exception:
            logger.exception('planning webserver crashed')
            return


def _open_browser_when_ready(url: str, logger) -> None:
    """Wait until the planning webserver answers, then open the browser tab.

    Off by default in CI / headless setups: set ``KATO_OPEN_BROWSER=0`` to
    skip. The poll runs in a daemon thread so kato's main loop never waits
    on the browser opening.
    """
    import os
    import threading
    import webbrowser

    if str(os.environ.get('KATO_OPEN_BROWSER', '1')).strip().lower() in {'0', 'false', 'no', 'off'}:
        return

    def _wait_and_open() -> None:
        if not _wait_for_planning_ui_healthz(url, logger=logger):
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
        for attr in ('resume_prompt_watcher', 'comment_run_watcher'):
            watcher = getattr(app, attr, None)
            if watcher is None:
                continue
            try:
                watcher.stop()
            except Exception:
                app.logger.exception('error stopping %s', attr)
        service = getattr(app, 'service', None)
        if service is not None:
            try:
                service.shutdown()
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


def _requeue_stuck_comments(app) -> None:
    """Re-queue local comments orphaned IN_PROGRESS by the last restart.

    Without this a comment whose agent was mid-run when kato stopped
    stays IN_PROGRESS forever: the scan-loop drain only re-dispatches
    QUEUED comments, and lazy resume (see the boot comment above
    ``_start_planning_webserver_if_enabled``) means the chat session
    is not respawned at boot — so the thread sits unaddressed and its
    tab stays "Claude: sleeping". Flipping it back to QUEUED lets the
    next scan tick pick it up and respawn the session. Best-effort:
    a failure here must never abort boot.
    """
    service = getattr(app, 'service', None)
    requeue = getattr(service, 'requeue_stuck_in_progress_comments', None)
    if not callable(requeue):
        return
    try:
        requeued = requeue()
    except Exception:
        app.logger.exception(
            'failed to requeue stuck in-progress comments at boot',
        )
        return
    if requeued:
        app.logger.info(
            'requeued %d comment(s) stuck in-progress from the previous '
            'run; _start_pending_comment_work will dispatch them next',
            len(requeued),
        )


def _log_known_session_ids(app) -> None:
    """Log every known Claude session id at kato startup.

    Operators use these IDs to cross-reference ``claude /status``
    output, find JSONL histories on disk, and diagnose stale state.
    Best-effort: any failure here must never abort boot.
    """
    service = getattr(app, 'service', None)
    session_manager = getattr(service, '_session_manager', None)
    if session_manager is None:
        return
    try:
        records = session_manager.list_records()
    except Exception:
        app.logger.exception('failed to list session records at boot')
        return
    if not records:
        app.logger.info('no known Claude session ids at startup')
        return
    lines = []
    for record in records:
        task_id = str(getattr(record, 'task_id', '') or '').strip()
        sid = read_session_id_from(record)
        if task_id and sid:
            lines.append(f'  task {task_id}: session id {sid}')
    if lines:
        app.logger.info(
            'known Claude session ids at startup:\n%s', '\n'.join(lines),
        )
    else:
        app.logger.info('no Claude session ids recorded at startup')


def _start_pending_comment_work(app) -> None:
    """At boot, immediately start the agent on every task that has a
    queued comment — don't make the operator wait for the first scan
    tick (startup delay + scan interval).

    Runs straight after ``_requeue_stuck_comments`` so a comment
    orphaned IN_PROGRESS by the previous run (now flipped back to
    QUEUED) is dispatched in the same boot pass: kato spawns/resumes
    the session and works on the comment right away instead of the
    tab sitting on "kato working" until a scan tick happens to come
    round. Best-effort: a failure here must never abort boot — the
    scan loop's own drain remains the backstop.
    """
    service = getattr(app, 'service', None)
    drain = getattr(service, 'drain_all_queued_task_comments', None)
    if not callable(drain):
        return
    try:
        started = drain()
    except Exception:
        app.logger.exception(
            'failed to dispatch queued comments at boot',
        )
        return
    if started:
        app.logger.info(
            'started agent work on %d task(s) with queued comments at boot',
            len(started),
        )


def _start_pending_comment_work_after_ui(app) -> None:
    """Dispatch queued comments after the planning UI has had first shot.

    The actual drain can spawn Claude sessions. Keep it off the boot
    path so a restart serves the UI first, then resumes queued local
    comment work in the background.
    """
    thread = threading.Thread(
        target=lambda: _start_pending_comment_work_when_ui_ready(app),
        name='kato-start-pending-comments',
        daemon=True,
    )
    thread.start()


def _start_pending_comment_work_when_ui_ready(app) -> None:
    url = str(getattr(app, 'planning_webserver_url', '') or '')
    if url and not _wait_for_planning_ui_healthz(url, logger=app.logger):
        app.logger.warning(
            'planning UI did not answer /healthz before queued-comment '
            'startup drain; dispatching queued comments anyway',
        )
    _start_pending_comment_work(app)


def _wait_for_planning_ui_healthz(
    url: str,
    *,
    timeout: float = 15.0,
    logger,
) -> bool:
    """Poll ``<url>/healthz`` until it answers or ``timeout`` elapses.

    Returns True once the endpoint responds, False on timeout. Callers
    decide what to log / do on timeout.
    """
    import urllib.error
    import urllib.request

    ssl_context = _healthz_ssl_context()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f'{url}/healthz', timeout=1, context=ssl_context,
            ):
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    return False


def _healthz_ssl_context():
    """An SSL context trusting ONLY kato's own generated local CA
    (never a blanket "skip verification") — this poll hits our own
    just-started ``https://127.0.0.1:.../healthz``, whose leaf cert is
    signed by that CA, not self-signed, so the CA (not the leaf) is the
    correct trust anchor. Returns None (default verification) when the
    CA doesn't exist yet — the normal state for plain-HTTP mode, where
    ``context`` is unused anyway. Best-effort: any read failure also
    falls back to None.
    """
    try:
        from kato_core_lib.helpers.tls_cert_utils import ca_paths
        ca_cert_path, _ca_key_path = ca_paths()
        if not ca_cert_path.is_file():
            return None
        import ssl
        return ssl.create_default_context(cafile=str(ca_cert_path))
    except Exception:
        return None


def _warm_up_repository_inventory(app) -> None:
    service = getattr(app, 'service', None)
    warm_up = getattr(service, 'warm_up_repository_inventory', None)
    if callable(warm_up):
        warm_up()


def _start_resume_prompt_watcher(app) -> None:
    """Background writer for per-task ``<workspace>/resume_prompt.md``.

    Polls live sessions every few seconds; whenever a Claude turn
    ends (a fresh ``result`` event lands in the session's recent-
    events buffer) it rewrites the markdown snapshot. The file is
    paste-into-Cursor-ready, so the operator can hand off the task
    to another agent without losing context.

    Best-effort: failure to start the watcher logs but doesn't
    block kato boot — kato runs fine without resume_prompt.md.
    """
    session_manager = getattr(app, 'session_manager', None)
    workspace_manager = getattr(app, 'workspace_manager', None)
    if session_manager is None or workspace_manager is None:
        app.logger.info(
            'resume_prompt watcher not started: session or workspace '
            'manager is not wired (headless / partial init)',
        )
        return
    try:
        from kato_core_lib.data_layers.service.resume_prompt_watcher import (
            build_and_start_resume_prompt_watcher,
        )
        watcher = build_and_start_resume_prompt_watcher(
            session_manager=session_manager,
            workspace_manager=workspace_manager,
        )
    except Exception:
        app.logger.exception(
            'failed to start resume_prompt watcher; kato will continue '
            'without auto-updated resume_prompt.md files',
        )
        return
    # Stash on the app so the shutdown hook can stop it cleanly.
    app.resume_prompt_watcher = watcher


def _start_comment_run_watcher(app) -> None:
    """Always-on, browser-independent drain of local comment runs.

    The ticket-scan loop's comment drain is gated by
    ``scan_interval_seconds`` (~180s, and disabled at 0) and the live
    SSE path only fires when a browser tab is watching that task — so a
    queued comment whose prior turn ended with no tab open was stranded.
    This watcher ticks the service's EXISTING advance/drain primitives
    every couple of seconds regardless, so the next comment starts
    promptly and the last one never hangs. Best-effort: a failure to
    start logs but never blocks boot — the scan-loop drain stays a
    backstop.
    """
    service = getattr(app, 'service', None)
    if service is None:
        return
    try:
        from kato_core_lib.data_layers.service.comment_run_watcher import (
            build_and_start_comment_run_watcher,
        )
        app.comment_run_watcher = build_and_start_comment_run_watcher(
            service=service,
        )
    except Exception:
        app.logger.exception(
            'failed to start comment-run watcher; queued comments will '
            'still drain via the scan loop / live SSE, just less promptly',
        )


def _task_scan_settings(cfg: DictConfig) -> float:
    task_scan_cfg = cfg.kato.get('task_scan', {}) or {}
    # Default 180s (3 min) matches the yaml. Slow enough that parallel
    # PR-lookups across (task × repo) don't trip Bitbucket / GitHub / GitLab
    # rate limits; fast enough that review-comment pickup feels responsive.
    # ``0`` disables the autonomous loop (operator must manually trigger scans).
    return float(task_scan_cfg.get('scan_interval_seconds', 180.0))


def _wait_for_boot_ready(
    ready_event: threading.Event, *, poll_seconds: float = 1.0,
) -> None:
    """Block until ``ready_event`` is set, polling so the main thread stays
    responsive to SIGINT/SIGTERM (the shutdown handler runs on this thread)
    instead of blocking uninterruptibly on a bare ``wait()``."""
    while not ready_event.wait(timeout=poll_seconds):
        continue


def _run_task_scan_loop(
    app,
    *,
    scan_interval_seconds: float,
    sleep_fn=time.sleep,
    max_cycles: int | None = None,
    force_scan_event: threading.Event | None = None,
    ready_event: threading.Event | None = None,
) -> None:
    job = ProcessAssignedTasksJob()
    job.initialized(app)
    # Manual-scan mode: ``scan_interval_seconds <= 0`` means "no
    # auto-poll". Kato stays alive and serves the webserver, but
    # no autonomous scan ever runs — every task pickup / review-
    # comment check has to be triggered by the operator via the
    # UI ("Scan now" in the tab strip, "Sync" on a task header,
    # or POST /api/scan/trigger). This is the default: the auto-
    # poll was hammering Bitbucket / GitHub / GitLab every 30s
    # with N parallel PR-lookups per (task × repo) and tripping
    # rate limits on multi-repo accounts. The operator can opt
    # the loop back in by setting ``scan_interval_seconds > 0``
    # in config or via the ``KATO_SCAN_INTERVAL_SECONDS`` env.
    #
    # The force-scan path (POST /api/scan/trigger) still works
    # without the loop because the webserver invokes
    # ``ProcessAssignedTasksJob.run()`` directly on the request
    # thread — see ``_register_scan_trigger_route``.
    if scan_interval_seconds <= 0:
        app.logger.info(
            'Autonomous scan loop disabled (scan_interval_seconds=%s). '
            'Use the UI "Scan now" / "Sync" buttons or '
            'POST /api/scan/trigger to run a scan on demand.',
            scan_interval_seconds,
        )
        return
    if ready_event is not None:
        # UI-first boot: block until the background finalize (connection
        # validation + reconciliation) is done before the first scan. If it
        # never completes we park here indefinitely — the webserver keeps
        # serving and the UI shows the validation error.
        _wait_for_boot_ready(ready_event)

    cycles = 0
    while True:
        if force_scan_event is not None:
            force_scan_event.clear()
        _SCAN_IN_PROGRESS.set()
        app.logger.info('Scanning for new tasks and reviews')
        try:
            job.run()
            app.logger.info('Scan complete')
        except Exception:
            app.logger.warning(
                'task scan failed; retrying in %s seconds',
                scan_interval_seconds,
            )
        finally:
            _SCAN_IN_PROGRESS.clear()

        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            return
        _idle_with_heartbeat(
            scan_interval_seconds,
            logger=app.logger,
            sleep_fn=sleep_fn,
            force_scan_event=force_scan_event,
        )


def _idle_with_heartbeat(
    interval_seconds: float,
    *,
    logger,
    sleep_fn=time.sleep,
    heartbeat_seconds: float = 5.0,
    force_scan_event: threading.Event | None = None,
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
        if force_scan_event is not None and force_scan_event.is_set():
            break
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
            # Use event.wait so a force-scan request wakes the loop
            # immediately instead of waiting out the full chunk.
            if force_scan_event is not None:
                force_scan_event.wait(timeout=chunk)
            else:
                sleep_fn(chunk)
        remaining -= chunk


if __name__ == '__main__':
    raise SystemExit(main())
