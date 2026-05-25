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
    """Return a project-prefixed logger.

    All loggers live under the ``paper_rag.`` namespace so external log
    shippers (ELK, Loki) can filter on a single prefix. Callers that pass
    a short name like ``"rag.qa_agentic"`` get auto-prefixed to
    ``"paper_rag.rag.qa_agentic"``. Callers that already pass a fully
    qualified name (starts with ``paper_rag.``) are left as-is.
    """
    if not _INITIALIZED:
        setup_logger()
    if name is None:
        full_name = "paper_rag"
    elif name == "paper_rag" or name.startswith("paper_rag."):
        full_name = name
    else:
        full_name = f"paper_rag.{name}"
    if _USE_LOGURU:
        from loguru import logger

        return logger.bind(scope=full_name)
    return logging.getLogger(full_name)
