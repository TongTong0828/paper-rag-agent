"""Stale paper reminder generator (M9 / ADR-0018).

Picks papers a user hasn't accessed in N days and emits inbox cards
nudging review. The card includes:
  - paper title
  - one-line system summary (from sqlite metadata, not LLM)
  - quick action: "Ask paper_qa" pre-fills with "Summarize <title>"
"""

from __future__ import annotations

import logging
import time
from typing import Any

from . import inbox, paper_access

log = logging.getLogger(__name__)


def _fetch_paper_meta(paper_id: str) -> dict[str, Any] | None:
    try:
        from ..store import sqlite_store

        row = sqlite_store.get_paper(paper_id)
    except Exception as e:
        log.warning("get_paper failed for %s: %s", paper_id, e)
        return None
    if row is None:
        return None
    if hasattr(row, "model_dump"):
        return row.model_dump()
    return dict(row) if isinstance(row, dict) else vars(row)


def stale_scan_for_user(
    user_id: str,
    *,
    older_than_days: int = 30,
    max_cards: int = 3,
) -> int:
    """Generate stale-paper inbox cards for one user. Returns count written."""
    stale_rows = paper_access.stale_for_user(
        user_id, older_than_days=older_than_days, limit=max_cards
    )
    if not stale_rows:
        return 0

    n_written = 0
    for row in stale_rows:
        meta = _fetch_paper_meta(row["paper_id"])
        if meta is None:
            continue
        title = meta.get("title", "Untitled")
        ago_days = int((time.time() - row["last_accessed_at"]) / 86400)

        body_md = (
            f"# 🕰 复习一下：{title}\n\n"
            f"> 你已经 **{ago_days} 天**没翻过这篇了。"
            f"过往访问过 **{row['access_count']}** 次。\n\n"
        )
        if meta.get("abstract"):
            body_md += "**摘要：**\n\n" + (meta["abstract"][:300] + "...") + "\n\n"

        inbox.write(
            user_id=user_id,
            kind="stale_paper",
            title=f"🕰 复习一下：{title[:60]}",
            body_md=body_md,
            actions=[
                {"label": "用一句话复习", "kind": "qa",
                 "arg": f"In one sentence: what's the core idea of {title}?"},
                {"label": "标已读", "kind": "mark_accessed", "arg": row["paper_id"]},
            ],
            related_paper_ids=[row["paper_id"]],
        )
        n_written += 1
        try:
            from paper_rag.observability.metrics import counter
            counter("paper_rag_proactive_stale_card_total").inc()
        except Exception:  # noqa: BLE001
            pass
    log.info("stale_scan: user=%s n_written=%d", user_id, n_written)
    return n_written


def stale_scan_for_all_users(*, older_than_days: int = 30, max_cards: int = 3) -> dict[str, Any]:
    """Cron entry. Iterates over users with any access record."""
    started = time.time()
    users = paper_access.list_users_with_access()
    n_written = 0
    for uid in users:
        try:
            n_written += stale_scan_for_user(uid, older_than_days=older_than_days, max_cards=max_cards)
        except Exception as e:
            log.exception("stale_scan_for_user(%s) failed: %s", uid, e)
    elapsed = time.time() - started
    log.info("stale_scan_all: users=%d cards=%d elapsed=%.1fs", len(users), n_written, elapsed)
    return {
        "n_users": len(users),
        "n_cards_written": n_written,
        "elapsed_sec": round(elapsed, 1),
    }


__all__ = ["stale_scan_for_user", "stale_scan_for_all_users"]
