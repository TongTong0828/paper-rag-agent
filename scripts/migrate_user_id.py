#!/usr/bin/env python3
"""ADR-0015 schema migration: add user_id columns for multi-user isolation.

Idempotent — safe to run multiple times. Existing rows get user_id='system'
(shared library, visible to all users).

Tables affected
---------------
- paper        : add user_id TEXT DEFAULT 'system' + index
- qa_history   : add user_id TEXT DEFAULT 'system' + index (only if table exists)
- wiki_entries : add user_id TEXT DEFAULT 'system' + index (only if table exists)
- wikientry    : alternative SQLModel-style table name fallback

Usage
-----
    python -m paper_rag.scripts.migrate_user_id        # uses default config
    PAPER_RAG_CONFIG=config/local.yaml python -m paper_rag.scripts.migrate_user_id
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))


def _add_user_id_column(con: sqlite3.Connection, table: str) -> str:
    """Add user_id column to `table` if missing. Returns one of:
    'added' | 'already_present' | 'table_missing'."""
    cur = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    if cur.fetchone() is None:
        return "table_missing"

    cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    if "user_id" in cols:
        return "already_present"

    con.execute(
        f"ALTER TABLE {table} ADD COLUMN user_id TEXT DEFAULT 'system'"
    )
    # Backfill any pre-existing NULLs (DEFAULT only applies to new INSERTs)
    con.execute(f"UPDATE {table} SET user_id = 'system' WHERE user_id IS NULL")
    con.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table}_user_id ON {table}(user_id)"
    )
    return "added"


def main() -> int:
    from paper_rag import config as cfg

    sqlite_path = cfg.load().paths.sqlite_path
    print(f"Migrating: {sqlite_path}")
    if not Path(sqlite_path).exists():
        # Bootstrap an empty DB by creating tables via SQLModel
        print("  database does not exist — creating fresh schema with user_id")
        from paper_rag.store import sqlite_store as _store
        _store.init_store()
        return 0

    con = sqlite3.connect(sqlite_path)
    try:
        for table in ("paper", "qa_history", "wiki_entries", "wikientry"):
            result = _add_user_id_column(con, table)
            print(f"  {table:18s} → {result}")
        con.commit()
    finally:
        con.close()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
