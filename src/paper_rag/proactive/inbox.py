"""Inbox — system → user notifications (M9 / ADR-0018).

Schema
------
- ``id``                PK
- ``user_id``           recipient
- ``kind``              daily_digest | sub_match | stale_paper | auto_ingest
- ``title``             short, used in list views
- ``body_md``           Markdown card body (rendered by frontend)
- ``actions_json``      list of {label, kind, arg} for action buttons
- ``related_paper_ids`` comma-separated, for filtering
- ``read_at``           NULL = unread
- ``dismissed_at``      NULL = not dismissed; non-NULL is soft-deleted
- ``created_at``        epoch sec
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)

INBOX_KINDS = ("daily_digest", "sub_match", "stale_paper", "auto_ingest")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body_md TEXT,
    actions_json TEXT,
    related_paper_ids TEXT,
    read_at REAL,
    dismissed_at REAL,
    created_at REAL NOT NULL DEFAULT (CAST(strftime('%s','now') AS REAL))
);
CREATE INDEX IF NOT EXISTS idx_inbox_user_unread ON inbox_items(user_id, read_at, created_at);
CREATE INDEX IF NOT EXISTS idx_inbox_kind ON inbox_items(kind);
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


def write(
    user_id: str,
    kind: str,
    title: str,
    *,
    body_md: str = "",
    actions: list[dict[str, Any]] | None = None,
    related_paper_ids: list[str] | None = None,
) -> int:
    if kind not in INBOX_KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {INBOX_KINDS}")
    if not title:
        raise ValueError("title required")

    actions_json = json.dumps(actions or [], ensure_ascii=False)
    pids = ",".join(related_paper_ids) if related_paper_ids else None

    with _connect() as con:
        cur = con.execute(
            "INSERT INTO inbox_items(user_id, kind, title, body_md, actions_json, related_paper_ids) "
            "VALUES(?,?,?,?,?,?)",
            (user_id, kind, title, body_md, actions_json, pids),
        )
        item_id = int(cur.lastrowid)

    # P3-13 / M11.5: best-effort webhook fan-out (DingTalk / Feishu / WeCom / email).
    # Only fires if the user has configured webhooks. Failures NEVER block.
    try:
        from . import webhook
        webhook.fan_out({
            "id": item_id,
            "user_id": user_id,
            "kind": kind,
            "title": title,
            "body_md": body_md,
        })
    except Exception:  # noqa: BLE001 — outbound network must not affect QA
        # Don't crash the inbox write, but make the failure visible — silent
        # webhook failures hide misconfigured endpoints in production.
        log.exception("webhook fan_out failed for item_id=%s user_id=%s", item_id, user_id)

    return item_id


def list_for_user(
    user_id: str,
    *,
    unread_only: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM inbox_items WHERE user_id=? AND dismissed_at IS NULL"
    if unread_only:
        sql += " AND read_at IS NULL"
    sql += " ORDER BY created_at DESC LIMIT ?"
    with _connect() as con:
        rows = con.execute(sql, (user_id, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["actions"] = json.loads(d.pop("actions_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["actions"] = []
        d["related_paper_ids"] = (d.get("related_paper_ids") or "").split(",") \
            if d.get("related_paper_ids") else []
        out.append(d)
    return out


def mark_read(item_id: int, *, user_id: str | None = None) -> bool:
    with _connect() as con:
        if user_id is not None:
            cur = con.execute(
                "UPDATE inbox_items SET read_at=? WHERE id=? AND user_id=? AND read_at IS NULL",
                (time.time(), item_id, user_id),
            )
        else:
            cur = con.execute(
                "UPDATE inbox_items SET read_at=? WHERE id=? AND read_at IS NULL",
                (time.time(), item_id),
            )
        return bool(cur.rowcount)


def dismiss(item_id: int, *, user_id: str | None = None) -> bool:
    with _connect() as con:
        if user_id is not None:
            cur = con.execute(
                "UPDATE inbox_items SET dismissed_at=? WHERE id=? AND user_id=? AND dismissed_at IS NULL",
                (time.time(), item_id, user_id),
            )
        else:
            cur = con.execute(
                "UPDATE inbox_items SET dismissed_at=? WHERE id=? AND dismissed_at IS NULL",
                (time.time(), item_id),
            )
        return bool(cur.rowcount)


def unread_count(user_id: str) -> int:
    with _connect() as con:
        row = con.execute(
            "SELECT COUNT(*) AS n FROM inbox_items "
            "WHERE user_id=? AND read_at IS NULL AND dismissed_at IS NULL",
            (user_id,),
        ).fetchone()
    return int(row["n"]) if row else 0


def purge_older_than(epoch: float) -> int:
    """Hard-delete read/dismissed items older than epoch."""
    with _connect() as con:
        cur = con.execute(
            "DELETE FROM inbox_items WHERE created_at < ? "
            "AND (read_at IS NOT NULL OR dismissed_at IS NOT NULL)",
            (epoch,),
        )
        return int(cur.rowcount)


__all__ = [
    "INBOX_KINDS",
    "write",
    "list_for_user",
    "mark_read",
    "dismiss",
    "unread_count",
    "purge_older_than",
]
