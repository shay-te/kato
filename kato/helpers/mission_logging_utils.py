from __future__ import annotations

_GREEN = '\033[32m'
_RESET = '\033[0m'


def _format_message(message: str, args: tuple) -> str:
    if not args:
        return message
    try:
        return message % args
    except Exception:
        return ' '.join([message, *[str(arg) for arg in args]])


def log_mission_step(logger, task_id: str, message: str, *args) -> None:
    logger.info('Mission %s: %s', task_id, _format_message(message, args))


def log_mission_start(logger, task_id: str, message: str, *args) -> None:
    logger.info('%s>> Mission %s: %s%s', _GREEN, task_id, _format_message(message, args), _RESET)


def log_mission_end(logger, task_id: str, message: str, *args) -> None:
    logger.info('%s<< Mission %s: %s%s', _GREEN, task_id, _format_message(message, args), _RESET)
