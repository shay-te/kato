import logging


_LOGGING_CONFIGURED = False


def configure_logger(name: str) -> logging.Logger:
    global _LOGGING_CONFIGURED

    if not _LOGGING_CONFIGURED:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s %(levelname)s %(name)s %(message)s',
        )
        _LOGGING_CONFIGURED = True

    return logging.getLogger(name)
