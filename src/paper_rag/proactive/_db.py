"""Shared SQLite connection helper for the proactive sub-package.

All proactive tables (inbox / webhook / subscriptions / paper_access) live in
the same ``feedback.sqlite`` file as the M11 feedback store. This module
centralises:

  - path resolution (``_resolve_path``)
  - connection setup (row factory, schema bootstrap)
  - the ``connect()`` context manager

so every proactive module just declares its own ``_SCHEMA`` string and calls
``connect(_SCHEMA)``. Consolidates ~64 lines of duplicated boilerplate
across inbox / webhook / subscriptions / paper_access.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def _resolve_path() -> Path:
    # Re-export feedback's single source of truth so changing the path
    # only requires touching one place.
    from ..feedback import store as feedback_store

    return feedback_store._resolve_path()


@contextmanager
def connect(schema_sql: str) -> Iterator[sqlite3.Connection]:
    """Open feedback.sqlite, ensure ``schema_sql`` is applied, and yield the
    connection. Commits on clean exit, always closes."""
    path = _resolve_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        con.executescript(schema_sql)
        yield con
        con.commit()
    finally:
        con.close()


__all__ = ["connect"]
