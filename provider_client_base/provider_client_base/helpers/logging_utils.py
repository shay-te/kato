from __future__ import annotations

import logging


def configure_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
