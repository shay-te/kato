from __future__ import annotations

import signal
import time

import hydra
from omegaconf import DictConfig

from kato.helpers.logging_utils import configure_logger
from kato.helpers.shell_status_utils import (
    sleep_with_scan_spinner,
    supports_inline_status,
    sleep_with_warmup_countdown,
)
from kato.helpers.status_broadcaster_utils import (
    StatusBroadcaster,
    install_status_broadcast_handler,
)
from kato.validate_env import validate_environment


_STATUS_BROADCASTER = StatusBroadcaster()
install_status_broadcast_handler(_STATUS_BROADCASTER)


class _ProcessAssignedTasksJobProxy:
    def __call__(self):
        from kato.jobs.process_assigned_tasks import ProcessAssignedTasksJob as _ProcessAssignedTasksJob

        return _ProcessAssignedTasksJob()


class _KatoInstanceProxy:
    @staticmethod
    def init(core_lib_cfg: DictConfig) -> None:
        from kato.kato_instance import KatoInstance as _KatoInstance

        _KatoInstance.init(core_lib_cfg)

    @staticmethod
    def get():
        from kato.kato_instance import KatoInstance as _KatoInstance

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
        KatoInstance.init(cfg)
    except RuntimeError as exc:
        if str(exc).startswith('startup dependency validation failed:') or str(exc).startswith('[Error] '):
            logger.error('%s', exc)
            return 1
        raise
    app = KatoInstance.get()
    app.logger = getattr(app, 'logger', None) or logger
    app.logger.info('Starting kato agent')
    _start_planning_webserver_if_enabled(app)
    _register_shutdown_hook(app)
    startup_delay_seconds, scan_interval_seconds = _task_scan_settings(cfg)
    _run_task_scan_loop(
        app,
        startup_delay_seconds=startup_delay_seconds,
        scan_interval_seconds=scan_interval_seconds,
    )
    return 0


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
    if session_manager is None:
        # Backends without a streaming session model (e.g. OpenHands) skip
        # the UI for now — there's nothing live to render.
        app.logger.info(
            'planning webserver skipped (no session manager — backend does not stream)'
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
        status_broadcaster=_STATUS_BROADCASTER,
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
        try:
            job.run()
        except Exception:
            app.logger.warning(
                'task scan failed; retrying in %s seconds',
                scan_interval_seconds,
            )

        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            return
        if scan_interval_seconds > 0:
            sleep_with_scan_spinner(
                scan_interval_seconds,
                status_text='Scanning for new tasks and comments',
                sleep_fn=sleep_fn,
            )


def _formatted_duration_text(seconds: float) -> str:
    normalized_seconds = float(seconds)
    rounded_seconds = int(normalized_seconds)
    if normalized_seconds == rounded_seconds:
        seconds_label = 'second' if rounded_seconds == 1 else 'seconds'
        return f'{rounded_seconds} {seconds_label}'
    return f'{normalized_seconds:.1f} seconds'


if __name__ == '__main__':
    raise SystemExit(main())
