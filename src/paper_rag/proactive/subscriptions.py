"""User subscriptions (M9 / ADR-0018).

Schema
------
- ``id``           PK
- ``user_id``      owner
- ``kind``         keyword | topic_vector | arxiv_category
- ``value``        keyword text / category code (cs.CL)
- ``strength``     low | normal | high → controls match similarity threshold
- ``enabled``      0/1
- ``created_at``   epoch sec
- ``last_matched_at`` epoch sec (NULL when never matched)
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


SUBSCRIPTION_KINDS = ("keyword", "topic_vector", "arxiv_category")
SUBSCRIPTION_STRENGTHS = ("low", "normal", "high")

# strength → similarity threshold. Higher strength = looser threshold (more matches).
STRENGTH_THRESHOLD = {
    "high": 0.55,    # strong interest: send everything moderately related
    "normal": 0.65,
    "low": 0.75,     # only super-precise matches
}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    strength TEXT NOT NULL DEFAULT 'normal',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL DEFAULT (CAST(strftime('%s','now') AS REAL)),
    last_matched_at REAL,
    UNIQUE(user_id, kind, value)
);
CREATE INDEX IF NOT EXISTS idx_sub_user ON subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_sub_enabled ON subscriptions(enabled);
"""


def _resolve_path() -> Path:
    """Reuse the same SQLite file as feedback_events (M11)."""
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


def add(user_id: str, kind: str, value: str, *, strength: str = "normal") -> int:
    """Add or reactivate a subscription. Returns id."""
    if kind not in SUBSCRIPTION_KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {SUBSCRIPTION_KINDS}")
    if strength not in SUBSCRIPTION_STRENGTHS:
        raise ValueError(f"unknown strength {strength!r}")
    if not value or not value.strip():
        raise ValueError("value must be non-empty")
    value = value.strip()

    with _connect() as con:
        cur = con.execute(
            "SELECT id FROM subscriptions WHERE user_id=? AND kind=? AND value=?",
            (user_id, kind, value),
        )
        row = cur.fetchone()
        if row:
            sid = int(row["id"])
            con.execute(
                "UPDATE subscriptions SET enabled=1, strength=? WHERE id=?",
                (strength, sid),
            )
            return sid
        cur = con.execute(
            "INSERT INTO subscriptions(user_id, kind, value, strength) VALUES(?,?,?,?)",
            (user_id, kind, value, strength),
        )
        return int(cur.lastrowid)


def list_for_user(
    user_id: str, *, kind: str | None = None, only_enabled: bool = True
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM subscriptions WHERE user_id=?"
    params: list = [user_id]
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    if only_enabled:
        sql += " AND enabled=1"
    sql += " ORDER BY created_at DESC"
    with _connect() as con:
        rows = con.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get(sub_id: int) -> dict[str, Any] | None:
    with _connect() as con:
        row = con.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    return dict(row) if row else None


def delete(sub_id: int, *, user_id: str | None = None) -> bool:
    """Soft-delete (enabled=0). user_id check prevents cross-user deletion."""
    with _connect() as con:
        if user_id is not None:
            cur = con.execute(
                "UPDATE subscriptions SET enabled=0 WHERE id=? AND user_id=?",
                (sub_id, user_id),
            )
        else:
            cur = con.execute("UPDATE subscriptions SET enabled=0 WHERE id=?", (sub_id,))
        return bool(cur.rowcount)


def toggle(sub_id: int, *, enabled: bool, user_id: str | None = None) -> bool:
    with _connect() as con:
        if user_id is not None:
            cur = con.execute(
                "UPDATE subscriptions SET enabled=? WHERE id=? AND user_id=?",
                (1 if enabled else 0, sub_id, user_id),
            )
        else:
            cur = con.execute(
                "UPDATE subscriptions SET enabled=? WHERE id=?",
                (1 if enabled else 0, sub_id),
            )
        return bool(cur.rowcount)


def mark_matched(sub_id: int) -> None:
    with _connect() as con:
        con.execute(
            "UPDATE subscriptions SET last_matched_at=? WHERE id=?",
            (time.time(), sub_id),
        )


def iter_active() -> Iterator[dict[str, Any]]:
    """Yield all enabled subscriptions across users (used by matcher)."""
    with _connect() as con:
        rows = con.execute(
            "SELECT * FROM subscriptions WHERE enabled=1"
        ).fetchall()
    for r in rows:
        yield dict(r)


__all__ = [
    "STRENGTH_THRESHOLD",
    "SUBSCRIPTION_KINDS",
    "SUBSCRIPTION_STRENGTHS",
    "add",
    "delete",
    "get",
    "iter_active",
    "list_for_user",
    "mark_matched",
    "toggle",
]
