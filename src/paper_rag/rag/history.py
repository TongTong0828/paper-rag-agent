"""Multi-turn conversation context.

Lets users say "What about the second one?" without re-stating the topic.

Implementation:
- conversation_id -> list of (question, answer, citations) tuples
- Stored in SQLite, lazy table.
- `with_history(question, conversation_id)` rewrites the question to be
  self-contained by asking the LLM to incorporate the recent dialog.

Usage:

    out = answer(
        question="What about the second one?",
        paper_ids=["arxiv:..."],
        conversation_id="user-123-session-A",
    )

`conversation_id=None` (default) preserves single-turn semantics; nothing
changes in qa_agentic for legacy callers.
"""

from __future__ import annotations

from datetime import datetime

from ..utils.logger import get_logger
from .llm import chat

log = get_logger("rag.history")
_TABLE_READY = False


def _ensure_table() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    from ..store.sqlite_store import get_engine

    with get_engine().begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS qa_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                citations_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS qa_history_conv_idx ON qa_history(conversation_id, id)"
        )
    _TABLE_READY = True


def append(conversation_id: str, question: str, answer: str, citations: list[str]) -> None:
    if not conversation_id:
        return
    import json as _json

    from sqlalchemy import text

    from ..store.sqlite_store import get_engine

    _ensure_table()
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO qa_history (conversation_id, question, answer, citations_json, created_at) "
                "VALUES (:c, :q, :a, :ci, :t)"
            ),
            {
                "c": conversation_id,
                "q": question,
                "a": answer[:2000],  # cap to keep table small
                "ci": _json.dumps(citations, ensure_ascii=False),
                "t": datetime.utcnow().isoformat(),
            },
        )


def recent(conversation_id: str, limit: int = 3) -> list[tuple[str, str]]:
    """Return last `limit` (question, answer) tuples in chronological order."""
    if not conversation_id:
        return []
    from sqlalchemy import text

    from ..store.sqlite_store import get_engine

    _ensure_table()
    with get_engine().begin() as conn:
        rows = list(
            conn.execute(
                text(
                    "SELECT question, answer FROM qa_history "
                    "WHERE conversation_id = :c ORDER BY id DESC LIMIT :n"
                ),
                {"c": conversation_id, "n": limit},
            )
        )
    return [(r[0], r[1]) for r in reversed(rows)]


_REWRITE_PROMPT = """You rewrite a follow-up question into a self-contained
research question, given the previous dialog.

Recent dialog (oldest first):
{history}

Current follow-up question: {q}

Rules:
- If the question is already self-contained (e.g. "What is X?"), return it unchanged.
- Otherwise, expand pronouns and ellipses by referring to the dialog.
- Output the rewritten question on a single line. No quotes, no explanation.
"""


def rewrite_with_history(question: str, conversation_id: str | None) -> str:
    """If conversation has history, ask LLM to make the question self-contained."""
    if not conversation_id:
        return question
    history = recent(conversation_id, limit=3)
    if not history:
        return question
    formatted = "\n".join(f"  Q: {q}\n  A: {a[:200]}" for q, a in history)
    prompt = _REWRITE_PROMPT.replace("{history}", formatted).replace("{q}", question)
    try:
        out = chat([{"role": "user", "content": prompt}], temperature=0, max_tokens=120)
        return out.strip().splitlines()[0] if out.strip() else question
    except Exception as e:
        log.warning(f"history rewrite failed: {e}; using original question")
        return question


__all__ = [
    "append",
    "recent",
    "rewrite_with_history",
]
