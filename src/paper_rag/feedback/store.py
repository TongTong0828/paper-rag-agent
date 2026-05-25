"""SQLite-backed feedback_events store (M11 / ADR-0017).

Lives in a SEPARATE file from paper_rag's main papers.sqlite — feedback
schema evolves frequently and we don't want to risk corrupting user
content during ALTER TABLE.

Default location: ``<index_dir>/feedback.sqlite`` (configurable via
``FEEDBACK_SQLITE_PATH`` env var for testing).

Idempotency
-----------
We hash (user_id, trace_id, event_type, minute_bucket) into a unique key.
Re-submitting the same event in the same minute returns the existing id
instead of duplicating. This protects against double-clicks, retries,
network jitter.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .events import FeedbackEvent

log = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    trace_id TEXT,
    conversation_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_user_time ON feedback_events(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_feedback_trace ON feedback_events(trace_id);
CREATE INDEX IF NOT EXISTS idx_feedback_type ON feedback_events(event_type);
"""


def _resolve_path() -> Path:
    """Resolve the feedback DB path from env or paper_rag config."""
    env = os.environ.get("FEEDBACK_SQLITE_PATH")
    if env:
        return Path(env).expanduser()
    try:
        from .. import config as cfg

        idx = Path(cfg.load().paths.index_dir).expanduser()
        return idx / "feedback.sqlite"
    except Exception as e:
        log.warning("could not load paper_rag config (%s); using cwd fallback", e)
        return Path("./feedback.sqlite").resolve()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    path = _resolve_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        # Apply schema (idempotent CREATE IF NOT EXISTS)
        con.executescript(_SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def _dedup_key(ev: FeedbackEvent) -> str:
    """Per-minute idempotency hash."""
    minute = int(ev.created_at // 60)
    raw = f"{ev.user_id}|{ev.trace_id or ''}|{ev.event_type}|{minute}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def write(ev: FeedbackEvent) -> int:
    """Persist an event. Returns the row id (existing id on dedup hit)."""
    if not ev.created_at:
        ev.created_at = time.time()
    key = _dedup_key(ev)
    payload = json.dumps(ev.payload, ensure_ascii=False, sort_keys=True)

    with _connect() as con:
        cur = con.execute(
            "SELECT id FROM feedback_events WHERE dedup_key = ?", (key,)
        )
        existing = cur.fetchone()
        if existing:
            return int(existing["id"])
        cur = con.execute(
            "INSERT INTO feedback_events "
            "(dedup_key, user_id, trace_id, conversation_id, event_type, "
            " payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                ev.user_id,
                ev.trace_id,
                ev.conversation_id,
                ev.event_type,
                payload,
                ev.created_at,
            ),
        )
        return int(cur.lastrowid)


def list_recent(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with _connect() as con:
        rows = con.execute(
            "SELECT id, trace_id, conversation_id, event_type, payload_json, created_at "
            "FROM feedback_events WHERE user_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"])
        except (json.JSONDecodeError, TypeError):
            payload = {}
        out.append({
            "id": r["id"],
            "trace_id": r["trace_id"],
            "conversation_id": r["conversation_id"],
            "event_type": r["event_type"],
            "payload": payload,
            "created_at": r["created_at"],
        })
    return out


def aggregate_user(user_id: str) -> dict[str, Any]:
    """Counts per event_type for a user. Cheap query, useful for self-stats UI."""
    with _connect() as con:
        rows = con.execute(
            "SELECT event_type, COUNT(*) AS n FROM feedback_events "
            "WHERE user_id = ? GROUP BY event_type",
            (user_id,),
        ).fetchall()
    by_type: dict[str, int] = {r["event_type"]: int(r["n"]) for r in rows}
    return {
        "user_id": user_id,
        "total_events": sum(by_type.values()),
        "by_type": by_type,
    }


def iter_since(epoch: float) -> Iterator[dict[str, Any]]:
    """Stream events with created_at >= epoch (used by hard_case_collector)."""
    with _connect() as con:
        rows = con.execute(
            "SELECT id, user_id, trace_id, conversation_id, event_type, "
            "payload_json, created_at FROM feedback_events "
            "WHERE created_at >= ? ORDER BY created_at",
            (epoch,),
        ).fetchall()
    for r in rows:
        try:
            payload = json.loads(r["payload_json"])
        except (json.JSONDecodeError, TypeError):
            payload = {}
        yield {
            "id": r["id"],
            "user_id": r["user_id"],
            "trace_id": r["trace_id"],
            "conversation_id": r["conversation_id"],
            "event_type": r["event_type"],
            "payload": payload,
            "created_at": r["created_at"],
        }


def purge_older_than(epoch: float) -> int:
    """Delete events older than epoch. Used by retention job."""
    with _connect() as con:
        cur = con.execute(
            "DELETE FROM feedback_events WHERE created_at < ?", (epoch,)
        )
        return int(cur.rowcount or 0)


__all__ = [
    "aggregate_user",
    "iter_since",
    "list_recent",
    "purge_older_than",
    "write",
]
