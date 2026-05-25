"""Auto-ingest hook (M9 / ADR-0018).

Detects arxiv URLs / IDs in chat user messages and triggers a background
ingest. Writes an ``auto_ingest`` inbox card when ingest completes (or
fails). Does NOT block the QA pipeline — the user gets their answer
immediately, the ingest happens in parallel.

Pure-logic helpers (URL extraction, dedup) are unit-tested. The actual
ingest call is patched in tests.
"""

from __future__ import annotations

import asyncio
import logging
import re

from . import inbox

log = logging.getLogger(__name__)


_ARXIV_PATTERNS = [
    re.compile(r"arxiv\.org/abs/(\d{4}\.\d{4,5})", re.I),
    re.compile(r"arxiv\.org/pdf/(\d{4}\.\d{4,5})", re.I),
    # Bare arxiv:2310.11511 form
    re.compile(r"\barxiv:(\d{4}\.\d{4,5})\b", re.I),
    # Bare 2310.11511 form (must be standalone — avoid matching dates etc)
    # Disabled by default; uncomment if false-positives are acceptable.
    # re.compile(r"\b(\d{4}\.\d{4,5})\b"),
]


def detect_arxiv_ids(text: str) -> list[str]:
    """Extract distinct arxiv IDs from a free-text user message."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for pat in _ARXIV_PATTERNS:
        for m in pat.findall(text):
            if m not in seen:
                seen.add(m)
                found.append(m)
    return found


def _ingest_one(arxiv_id: str, user_id: str) -> dict:
    """Lazy import + call. Returns paper_index result dict."""
    try:
        from ..tools.paper_index import ingest as ingest_tool
    except ImportError as e:
        return {"status": "error", "error": f"paper_rag.tools.paper_index unavailable: {e}"}

    try:
        return ingest_tool({
            "arxiv_id": arxiv_id,
            "user_id": user_id,
        })
    except Exception as e:
        log.exception("auto_ingest failed for %s: %s", arxiv_id, e)
        return {"status": "error", "error": str(e), "arxiv_id": arxiv_id}


def background_ingest_sync(arxiv_id: str, user_id: str) -> int:
    """Synchronous version (for tests / scripts). Returns inbox item id."""
    result = _ingest_one(arxiv_id, user_id)
    return _write_inbox_card(arxiv_id, user_id, result)


async def background_ingest(arxiv_id: str, user_id: str) -> int:
    """Async wrapper that runs the (sync) ingest in a thread."""
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _ingest_one, arxiv_id, user_id)
    return _write_inbox_card(arxiv_id, user_id, result)


def _write_inbox_card(arxiv_id: str, user_id: str, result: dict) -> int:
    is_error = result.get("status") == "error" or "error" in result
    try:
        from paper_rag.observability.metrics import counter
        counter("paper_rag_proactive_auto_ingest_total",
                {"status": "error" if is_error else "success"}).inc()
    except Exception:
        pass

    if is_error:
        body = (
            f"## ❌ 入库失败：arxiv:{arxiv_id}\n\n"
            f"```\n{result.get('error', 'unknown error')}\n```\n"
        )
        return inbox.write(
            user_id=user_id,
            kind="auto_ingest",
            title=f"❌ 入库失败 arxiv:{arxiv_id}",
            body_md=body,
            actions=[
                {"label": "重试", "kind": "ingest", "arg": arxiv_id},
            ],
        )
    paper_id = result.get("paper_id") or f"arxiv:{arxiv_id}"
    title = result.get("title") or "Untitled"
    n_chunks = result.get("n_chunks", 0)
    body = (
        f"## ✅ 已为你入库\n\n"
        f"**{title}** (arxiv:{arxiv_id}) — {n_chunks} chunks 索引完成。\n\n"
        f"现在你可以问关于这篇的任何问题了。"
    )
    return inbox.write(
        user_id=user_id,
        kind="auto_ingest",
        title=f"✅ 已入库 arxiv:{arxiv_id}",
        body_md=body,
        actions=[
            {"label": "用一句话总结", "kind": "qa",
             "arg": f"In one sentence: what's the contribution of {title}?"},
            {"label": "查看论文", "kind": "open_paper", "arg": paper_id},
        ],
        related_paper_ids=[paper_id],
    )


def schedule_for_text(text: str, user_id: str) -> list[str]:
    """Schedule async ingest for any arxiv IDs in `text`. Returns scheduled IDs.

    Only call from inside an asyncio loop; from sync contexts use
    ``background_ingest_sync`` directly per id.
    """
    ids = detect_arxiv_ids(text)
    for aid in ids:
        try:
            asyncio.create_task(background_ingest(aid, user_id))
        except RuntimeError:
            # Not inside an event loop — fall back to sync (rare path)
            background_ingest_sync(aid, user_id)
    return ids


__all__ = [
    "background_ingest",
    "background_ingest_sync",
    "detect_arxiv_ids",
    "schedule_for_text",
]
