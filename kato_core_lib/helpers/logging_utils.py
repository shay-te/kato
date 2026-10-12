from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from agent_core_lib.agent_core_lib.helpers.logging_utils import (
    set_workflow_root as _set_agent_workflow_root,
)
from kato_core_lib.helpers.kato_paths_utils import kato_home_path


_LOGGING_CONFIGURED = False
_ROOT_HANDLER_NAME = 'kato_root'
_WORKFLOW_HANDLER_NAME = 'kato_workflow'
_FILE_HANDLER_NAME = 'kato_file'
# Crashes go to the log file only — the console prints its own traceback.
_CRASH_LOGGER_NAME = 'kato.crash'
LOG_FILE_ENV = 'KATO_LOG_FILE'
_LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
_LOG_FILE_BACKUPS = 3
_WORKFLOW_LOGGER_PREFIX = 'kato.workflow'
_DEFAULT_LOG_LEVEL = logging.WARNING
_DEFAULT_WORKFLOW_LOG_LEVEL = logging.INFO

# Re-root agent_core_lib's SHARED logger namespace under kato's, so the
# transport (Claude/Codex/OpenHands) loggers — which use agent_core_lib's
# configure_logger — parent under ``kato.workflow`` exactly as kato's own
# services do. Without this, agent_core_lib's generic default (``agent.workflow``)
# would orphan transport logs from the status broadcaster + KATO_WORKFLOW_LOG_LEVEL
# control. Runs at first import of this ubiquitously-imported module, before any
# transport logger is created.
_set_agent_workflow_root(_WORKFLOW_LOGGER_PREFIX)


def _configured_log_level(env_key: str, default_name: str, fallback_level: int) -> int:
    configured_name = str(os.getenv(env_key, default_name) or '').strip().upper()
    return getattr(logging, configured_name, fallback_level)


def _dependency_log_level() -> int:
    return _configured_log_level(
        'KATO_LOG_LEVEL',
        'warning',
        _DEFAULT_LOG_LEVEL,
    )


def _workflow_log_level() -> int:
    return _configured_log_level(
        'KATO_WORKFLOW_LOG_LEVEL',
        'info',
        _DEFAULT_WORKFLOW_LOG_LEVEL,
    )


def harden_stream_encoding() -> None:
    """Make stdout/stderr incapable of crashing on a non-ASCII character.

    Reported from a Windows host, on every chat message::

        UnicodeEncodeError: 'charmap' codec can't encode character
        '\\u2192' in position 102: character maps to <undefined>

    A redirected stream on Windows defaults to cp1252, which cannot encode
    the ``->`` arrow in a routing line. ``logging`` catches the failure and
    prints a full traceback instead of the line, so the operator loses the
    message and gains a stack trace — several per message.

    Sweeping non-ASCII out of kato's own format strings would NOT fix this,
    which is the important part: the arguments are operator data — ticket
    summaries, branch names, file paths, agent output — and can contain
    anything at all. The stream is the one place that covers both sides.

    ``errors='replace'`` is the guarantee: whatever encoding we end up on,
    an unencodable character degrades to ``?`` instead of raising. UTF-8 is
    preferred first because a redirected stream is read back by something
    that can decode it; if that is refused we keep the console's own
    encoding and only add the error handler.

    Idempotent, and safe to call when ``sys.stdout`` has been replaced by an
    object without ``reconfigure`` (pytest capture, a custom tee).

    ``raiseExceptions = False`` is the SECOND guarantee, and the one that
    actually protects the request. Reconfiguring covers every stream we can
    reach, but a handler we do not own — or a stream that refuses both
    attempts above — could still fail, and ``handleError`` guards only
    ``OSError``. With the flag off, a logging failure is silently dropped
    instead of propagating into the caller. Losing a log line is bad; 500ing
    the POST that carries the operator's message, so the agent never receives
    it, is much worse. The cost is that ``--- Logging error ---`` reports stop
    being printed; with the stream hardened there should be none left to see.
    """
    logging.raiseExceptions = False
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding='utf-8', errors='replace')
        except (OSError, ValueError, LookupError):
            try:
                reconfigure(errors='replace')
            except (OSError, ValueError, LookupError):
                # Nothing more to try — a stream that refuses both is one we
                # must not take the process down over.
                pass


def _named_handler(logger: logging.Logger, handler_name: str) -> logging.Handler | None:
    for handler in logger.handlers:
        if handler.get_name() == handler_name:
            return handler
    return None


def _ensure_root_logging() -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.NOTSET)
    handler = _named_handler(root_logger, _ROOT_HANDLER_NAME)
    if handler is None:
        handler = logging.StreamHandler()
        handler.set_name(_ROOT_HANDLER_NAME)
        root_logger.addHandler(handler)
    handler.setLevel(_dependency_log_level())
    handler.setFormatter(logging.Formatter('%(message)s'))


def _ensure_workflow_logging() -> None:
    workflow_logger = logging.getLogger(_WORKFLOW_LOGGER_PREFIX)
    workflow_logger.setLevel(_workflow_log_level())
    workflow_logger.propagate = False
    handler = _named_handler(workflow_logger, _WORKFLOW_HANDLER_NAME)
    if handler is None:
        handler = logging.StreamHandler()
        handler.set_name(_WORKFLOW_HANDLER_NAME)
        workflow_logger.addHandler(handler)
    handler.setLevel(_workflow_log_level())
    handler.setFormatter(logging.Formatter('%(message)s'))


def _workflow_logger_name(name: str) -> str:
    suffix = str(name or '').strip().replace(' ', '_').replace('-', '_').replace('.', '_')
    if not suffix:
        return _WORKFLOW_LOGGER_PREFIX
    return f'{_WORKFLOW_LOGGER_PREFIX}.{suffix}'


def configure_logger(name: str) -> logging.Logger:
    global _LOGGING_CONFIGURED

    if not _LOGGING_CONFIGURED:
        # BEFORE the handlers exist. They capture ``sys.stderr`` by reference
        # and ``reconfigure`` mutates that object in place, so the order is
        # not load-bearing — but a handler that logs during setup should
        # already be on a stream that cannot raise.
        harden_stream_encoding()
        _ensure_root_logging()
        _ensure_workflow_logging()
        _LOGGING_CONFIGURED = True

    return logging.getLogger(_workflow_logger_name(name))


# ----- the log file --------------------------------------------------------
#
# kato logged only to the console. A crash under the desktop app, or in a
# terminal closed since, left nothing to read — on Windows, nothing at all
# ("where does it store the crash report?"). Now the same lines also go to a
# file under kato's own folder, on every platform and however kato started.

def log_file_path() -> Path:
    """``~/.kato/logs/kato.log`` — ``%USERPROFILE%\\.kato\\logs`` on Windows,
    which ``Path.home()`` resolves there (Windows sets no ``HOME``)."""
    return kato_home_path(os.path.join('logs', 'kato.log'), env_key=LOG_FILE_ENV)


class _ConsoleLevels(logging.Filter):
    """Keep the file to what the console shows: kato's workflow lines at their
    level, everything else at the dependency level — both as the console's
    handlers took them at boot, so a setting loaded later cannot make the two
    disagree."""

    def __init__(self) -> None:
        super().__init__()
        self._workflow_level = _workflow_log_level()
        self._dependency_level = _dependency_log_level()

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        if name == _CRASH_LOGGER_NAME:
            return True
        workflow = name == _WORKFLOW_LOGGER_PREFIX or name.startswith(f'{_WORKFLOW_LOGGER_PREFIX}.')
        return record.levelno >= (self._workflow_level if workflow else self._dependency_level)


def enable_log_file() -> Path | None:
    """Write what kato logs to :func:`log_file_path` too, timestamped, and
    every unhandled exception — main thread or any other — with its traceback.

    Rotates at 5 MB, keeping 3. Idempotent. Returns the path, or None when the
    file cannot be opened: then the console says so, and kato runs on — a log
    file is never a reason not to start.
    """
    configure_logger('logging')
    root = logging.getLogger()
    existing = _named_handler(root, _FILE_HANDLER_NAME)
    if existing is not None:
        return Path(existing.baseFilename)
    path = log_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=_LOG_FILE_MAX_BYTES, backupCount=_LOG_FILE_BACKUPS,
            encoding='utf-8', errors='replace',
        )
    except OSError as exc:
        configure_logger('logging').warning('kato could not open its log file %s: %s', path, exc)
        return None
    handler.set_name(_FILE_HANDLER_NAME)
    # The pid tells a restart (a new process, on Windows) from the kato before it.
    handler.setFormatter(logging.Formatter('%(asctime)s [%(process)d] %(levelname)s %(name)s: %(message)s'))
    handler.addFilter(_ConsoleLevels())
    root.addHandler(handler)
    # The workflow logger does not propagate to the root, so it gets the
    # same handler itself.
    logging.getLogger(_WORKFLOW_LOGGER_PREFIX).addHandler(handler)
    crash = logging.getLogger(_CRASH_LOGGER_NAME)
    crash.propagate = False
    crash.addHandler(handler)
    _record_crashes(crash)
    return path


def _record_crashes(crash: logging.Logger) -> None:
    """Every unhandled exception into the log file, then the usual handling.

    Installed once: ``crash`` is a named logger, so a hook already in place
    reaches whatever file handler it has now — wrapping again would write each
    crash twice.
    """
    if getattr(sys.excepthook, '_records_kato_crashes', False) is True:
        return
    previous_hook = sys.excepthook
    previous_thread_hook = threading.excepthook

    def log_crash(exc_type, exc, traceback):
        if not issubclass(exc_type, KeyboardInterrupt):
            crash.critical('kato crashed', exc_info=(exc_type, exc, traceback))
        previous_hook(exc_type, exc, traceback)

    def log_thread_crash(args):
        if not issubclass(args.exc_type, (KeyboardInterrupt, SystemExit)):
            thread = getattr(args.thread, 'name', '?')
            crash.critical(
                'kato thread %s crashed', thread,
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        previous_thread_hook(args)

    log_crash._records_kato_crashes = True
    sys.excepthook = log_crash
    threading.excepthook = log_thread_crash
