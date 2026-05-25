"""Lightweight question-answer cache.

Why: same question asked twice within a short window is common in research
(user re-runs a query to copy-paste). A 24h cache keyed on a normalized
question saves both LLM tokens and wall time.

Backed by SQLite. Lazy table creation. Cache key = sha1(normalized_question
+ "|" + sorted_paper_ids). Stored value = JSON of `qa_agentic.answer`
output (minus the heavy `chunks` field, only chunk_ids preserved).

Disabled by default; enable via `rag.qa_cache.enabled: true`.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta

from .. import config as cfg
from ..utils.logger import get_logger

log = get_logger("rag.qa_cache")
_TABLE_READY = False


def _norm_question(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").strip().lower())


def _make_key(question: str, paper_ids: list[str] | None) -> str:
    base = _norm_question(question) + "|" + ",".join(sorted(paper_ids or []))
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def _ensure_table() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    from ..store.sqlite_store import get_engine

    engine = get_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS qa_cache (
                key TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                paper_ids TEXT NOT NULL,
                answer_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
    _TABLE_READY = True


def get(question: str, paper_ids: list[str] | None) -> dict | None:
    if not _enabled():
        return None
    _ensure_table()
    from sqlalchemy import text

    from ..store.sqlite_store import get_engine

    key = _make_key(question, paper_ids)
    with get_engine().begin() as conn:
        row = conn.execute(
            text("SELECT answer_json, created_at FROM qa_cache WHERE key = :k"),
            {"k": key},
        ).first()
    if not row:
        return None
    answer_json, created_at = row
    age = datetime.utcnow() - datetime.fromisoformat(created_at)
    ttl = timedelta(hours=cfg.load().rag.qa_cache_ttl_hours)
    if age > ttl:
        log.info(f"qa_cache stale ({age}); evicting")
        _evict(key)
        return None
    log.info(f"qa_cache HIT (age={age})")
    return json.loads(answer_json)


def put(question: str, paper_ids: list[str] | None, answer: dict) -> None:
    if not _enabled():
        return
    _ensure_table()
    from sqlalchemy import text

    from ..store.sqlite_store import get_engine

    key = _make_key(question, paper_ids)
    payload = {
        "answer": answer.get("answer"),
        "citations": answer.get("citations", []),
        "chunk_ids": [c.get("chunk_id") for c in (answer.get("chunks") or []) if c.get("chunk_id")],
        "trace": answer.get("trace"),
        "suspicious_citations": answer.get("suspicious_citations"),
    }
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT OR REPLACE INTO qa_cache (key, question, paper_ids, answer_json, created_at) "
                "VALUES (:k, :q, :p, :a, :t)"
            ),
            {
                "k": key,
                "q": question,
                "p": ",".join(sorted(paper_ids or [])),
                "a": json.dumps(payload, ensure_ascii=False),
                "t": datetime.utcnow().isoformat(),
            },
        )


def _evict(key: str) -> None:
    from sqlalchemy import text

    from ..store.sqlite_store import get_engine

    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM qa_cache WHERE key = :k"), {"k": key})


def _enabled() -> bool:
    return cfg.load().rag.qa_cache_enabled
