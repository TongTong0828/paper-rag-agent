"""Per-user-paper access tracking (M9 / ADR-0018).

Used by the stale-paper detector. Updated each time a user query / search /
wiki lookup actually surfaces a paper to them. To minimize write contention
with the QA hot path, callers SHOULD record asynchronously
(``asyncio.create_task``).

Schema
------
    PRIMARY KEY (user_id, paper_id)
    last_accessed_at REAL
    access_count INT default 1
"""

from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_access (
    user_id TEXT NOT NULL,
    paper_id TEXT NOT NULL,
    last_accessed_at REAL NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, paper_id)
);
CREATE INDEX IF NOT EXISTS idx_pa_user_time ON paper_access(user_id, last_accessed_at);
"""


def _resolve_path() -> Path:
    from ..feedback import store as feedback_store

    return feedback_store._resolve_path()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    path = _resolve_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def touch(user_id: str, paper_id: str, *, ts: float | None = None) -> None:
    """Record an access. Increments count if (user, paper) already exists."""
    if not user_id or not paper_id:
        return
    ts = ts if ts is not None else time.time()
    with _connect() as con:
        con.execute(
            """INSERT INTO paper_access(user_id, paper_id, last_accessed_at, access_count)
               VALUES(?, ?, ?, 1)
               ON CONFLICT(user_id, paper_id) DO UPDATE SET
                   last_accessed_at=excluded.last_accessed_at,
                   access_count = paper_access.access_count + 1""",
            (user_id, paper_id, ts),
        )


def touch_many(user_id: str, paper_ids: list[str], *, ts: float | None = None) -> None:
    """Bulk version. Used in qa_agentic answer hook."""
    if not user_id or not paper_ids:
        return
    ts = ts if ts is not None else time.time()
    with _connect() as con:
        con.executemany(
            """INSERT INTO paper_access(user_id, paper_id, last_accessed_at, access_count)
               VALUES(?, ?, ?, 1)
               ON CONFLICT(user_id, paper_id) DO UPDATE SET
                   last_accessed_at=excluded.last_accessed_at,
                   access_count = paper_access.access_count + 1""",
            [(user_id, pid, ts) for pid in paper_ids],
        )


def stale_for_user(
    user_id: str,
    *,
    older_than_days: int = 30,
    limit: int = 5,
) -> list[dict]:
    """Return papers user_id hasn't accessed in `older_than_days` days, oldest first."""
    cutoff = time.time() - older_than_days * 86400
    with _connect() as con:
        rows = con.execute(
            "SELECT paper_id, last_accessed_at, access_count FROM paper_access "
            "WHERE user_id=? AND last_accessed_at < ? "
            "ORDER BY last_accessed_at ASC LIMIT ?",
            (user_id, cutoff, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def list_users_with_access() -> list[str]:
    """Distinct user_ids with at least one access record. Used for cron fan-out."""
    with _connect() as con:
        rows = con.execute(
            "SELECT DISTINCT user_id FROM paper_access"
        ).fetchall()
    return [r["user_id"] for r in rows]


__all__ = ["touch", "touch_many", "stale_for_user", "list_users_with_access"]
