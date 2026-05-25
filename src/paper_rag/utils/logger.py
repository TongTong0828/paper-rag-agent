"""Logger setup. Prefers loguru; falls back to stdlib logging when loguru
is not installed (e.g. before deps installed)."""

from __future__ import annotations

import logging
import sys

_INITIALIZED = False
_USE_LOGURU = False


def setup_logger(level: str = "INFO", json: bool = False) -> None:
    global _INITIALIZED, _USE_LOGURU
    if _INITIALIZED:
        return
    try:
        from loguru import logger as _l

        _l.remove()
        if json:
            _l.add(sys.stderr, level=level, serialize=True)
        else:
            _l.add(
                sys.stderr,
                level=level,
                format=(
                    "<green>{time:HH:mm:ss}</green> "
                    "<level>{level: <7}</level> "
                    "<cyan>{name}</cyan> - <level>{message}</level>"
                ),
            )
        _USE_LOGURU = True
    except ImportError:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)-7s %(name)s - %(message)s",
            datefmt="%H:%M:%S",
        )
    _INITIALIZED = True


def get_logger(name: str | None = None):
    if not _INITIALIZED:
        setup_logger()
    if _USE_LOGURU:
        from loguru import logger

        return logger.bind(scope=name) if name else logger
    return logging.getLogger(name or "paper_rag")
