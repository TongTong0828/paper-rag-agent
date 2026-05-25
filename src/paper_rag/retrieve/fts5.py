"""SQLite FTS5-based sparse retrieval.

Why FTS5 over rank_bm25:

- **Zero extra deps** (sqlite is already used)
- **Incremental update**: insert one chunk -> one INSERT, no full rebuild
- **Persistent**: lives in the same .sqlite file as `chunks`
- **Scales to ~1M docs** on a single host

How it works:

- We mirror text chunk content into an `chunks_fts` virtual table (created
  lazily on first use).
- The triggers `chunks_ai/_au/_ad` keep it in sync with the main `chunks`
  table.
- `bm25(table)` ranking function is built into FTS5 (smaller score == better;
  we negate so callers can keep "higher is better" convention).

Tokenization: we use `unicode61 remove_diacritics 2 tokenchars '_'` plus a
`porter` stemmer for English. CJK characters fall through unicode61's
default segmentation (which is per-character — same behaviour as the
rank_bm25 fallback).
"""

from __future__ import annotations

import re

from ..utils.logger import get_logger

log = get_logger("retrieve.fts5")

_INITIALIZED = False


def _engine():
    from ..store.sqlite_store import get_engine

    return get_engine()


def _ensure_table() -> None:
    """Create FTS5 virtual table + sync triggers (idempotent)."""
    global _INITIALIZED
    if _INITIALIZED:
        return

    engine = _engine()
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id UNINDEXED,
                paper_id UNINDEXED,
                modality UNINDEXED,
                text,
                tokenize = "unicode61 remove_diacritics 2 tokenchars '_'"
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunk
            BEGIN
                INSERT INTO chunks_fts(chunk_id, paper_id, modality, text)
                VALUES (new.chunk_id, new.paper_id, new.modality, new.text);
            END
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunk
            BEGIN
                DELETE FROM chunks_fts WHERE chunk_id = old.chunk_id;
            END
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunk
            BEGIN
                DELETE FROM chunks_fts WHERE chunk_id = old.chunk_id;
                INSERT INTO chunks_fts(chunk_id, paper_id, modality, text)
                VALUES (new.chunk_id, new.paper_id, new.modality, new.text);
            END
            """
        )
    _INITIALIZED = True
    log.info("FTS5 chunks_fts ready")


def reindex_all() -> int:
    """Rebuild FTS5 from scratch (use after a schema change or initial load)."""
    _ensure_table()
    engine = _engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM chunks_fts")
        conn.exec_driver_sql(
            """
            INSERT INTO chunks_fts(chunk_id, paper_id, modality, text)
            SELECT chunk_id, paper_id, modality, text FROM chunk
            """
        )
        n = conn.exec_driver_sql("SELECT COUNT(*) FROM chunks_fts").scalar() or 0
    log.info(f"FTS5 reindexed n={n}")
    return int(n)


# FTS5 MATCH syntax requires escaping certain chars. Conservative approach:
# split on whitespace, drop empty tokens, wrap each in double quotes to make
# them phrase atoms (FTS5 treats "foo bar" as a phrase; we OR the atoms).
_TOKEN_SPLIT_RE = re.compile(r"\s+")


def _build_match_query(query: str) -> str:
    raw_tokens = _TOKEN_SPLIT_RE.split(query.strip())
    safe = []
    for t in raw_tokens:
        # Strip FTS5-special chars; keep alnum, CJK, underscore.
        cleaned = re.sub(r"[^\w\u4e00-\u9fff]", "", t)
        if cleaned:
            safe.append(f'"{cleaned}"')
    return " OR ".join(safe) if safe else '""'


def search(query: str, top_k: int = 20, paper_ids: list[str] | None = None) -> list[dict]:
    """Return top-k FTS5 matches with negated bm25 score (higher = better)."""
    _ensure_table()
    match_q = _build_match_query(query)
    if match_q == '""':
        return []

    engine = _engine()
    sql = (
        "SELECT chunk_id, paper_id, modality, text, "
        "  -bm25(chunks_fts) AS score "
        "FROM chunks_fts "
        "WHERE chunks_fts MATCH :q "
    )
    params: dict = {"q": match_q}
    if paper_ids:
        placeholders = ",".join(f":p{i}" for i in range(len(paper_ids)))
        sql += f"  AND paper_id IN ({placeholders}) "
        for i, pid in enumerate(paper_ids):
            params[f"p{i}"] = pid
    sql += "ORDER BY bm25(chunks_fts) LIMIT :k"
    params["k"] = top_k

    out: list[dict] = []
    from sqlmodel import Session

    with Session(engine) as s:
        rows = s.exec(_text_clause(sql), params=params).all()  # type: ignore[arg-type]
    for rank, row in enumerate(rows):
        out.append({
            "chunk_id": row[0],
            "paper_id": row[1],
            "modality": row[2],
            "text": row[3],
            "score_bm25": float(row[4]),
            "rank_bm25": rank,
        })
    return out


def _text_clause(sql: str):
    from sqlalchemy import text

    return text(sql)
