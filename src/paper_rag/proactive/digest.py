"""Daily arxiv digest (M9 / ADR-0018).

For each user with keyword subscriptions:
  1. Fetch arxiv papers for each keyword (past 24h)
  2. Generate 50-word TL;DR per paper using small_model
  3. Render Markdown card → write inbox

To minimize cost, TL;DR generation is shared across users (same paper →
same summary). The cache is in-process per worker (good enough; restart
flushes it).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from . import inbox, subscriptions

log = logging.getLogger(__name__)


# In-process TL;DR cache, keyed by arxiv_id (or sha1 of title+abstract).
_TLDR_CACHE: dict[str, str] = {}


def _tldr(paper: dict[str, Any]) -> str:
    """Generate or fetch a 50-word TL;DR. Falls back to abstract truncation."""
    key = paper.get("arxiv_id") or paper.get("paper_id") or paper.get("title", "")[:80]
    if key in _TLDR_CACHE:
        return _TLDR_CACHE[key]

    title = paper.get("title", "")
    abstract = paper.get("abstract") or paper.get("summary") or ""
    prompt = (
        f"In ≤50 words, summarize this paper for a researcher's morning briefing.\n"
        f"Title: {title}\nAbstract: {abstract[:1500]}\n"
        f"Output ONLY the summary, no preamble."
    )
    try:
        from ..rag.llm import chat
        summary = chat(
            [
                {"role": "system", "content": "You write tight, factual abstract summaries."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=120,
        ).strip()
    except Exception as e:
        log.warning("TL;DR LLM call failed (%s); using abstract truncation", e)
        summary = (abstract[:200] + "...") if abstract else "(no abstract)"
    _TLDR_CACHE[key] = summary
    return summary


def _fetch_papers_for_keyword(keyword: str, *, days: int = 1, max_results: int = 5) -> list[dict]:
    """Fetch arxiv papers matching a keyword from the past N days."""
    try:
        from ..source import arxiv_source
    except ImportError as e:
        log.warning("arxiv_source unavailable: %s", e)
        return []
    try:
        return arxiv_source.search_recent(keyword, days=days, max_results=max_results)
    except AttributeError:
        # Fallback: older API surface
        try:
            return arxiv_source.search(keyword, max_results=max_results)
        except Exception as e:
            log.warning("arxiv_source.search failed: %s", e)
            return []
    except Exception as e:
        log.warning("arxiv_source fetch failed for %s: %s", keyword, e)
        return []


def _dedup(papers: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for p in papers:
        key = p.get("arxiv_id") or p.get("paper_id") or p.get("title", "")
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def render_digest_card(bullets: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (title, body_md) for the inbox item."""
    n = len(bullets)
    if n == 0:
        return ("📰 今日 arxiv 简报：暂无新内容", "今天没有匹配你订阅的新论文。")

    lines = [f"# 今日 arxiv 简报（{n} 篇）", ""]
    for b in bullets:
        p = b["paper"]
        title = p.get("title", "Untitled")
        aid = p.get("arxiv_id") or p.get("paper_id") or ""
        link = f"https://arxiv.org/abs/{aid}" if aid else ""
        lines.append(f"## [{title}]({link})")
        lines.append(f"_arxiv:{aid}_  (matched: `{b['matched_keyword']}`)" if aid else f"(matched: `{b['matched_keyword']}`)")
        lines.append("")
        lines.append(b["tldr"])
        lines.append("")
    return (
        f"📰 今日 arxiv 简报（{n} 篇）",
        "\n".join(lines),
    )


def daily_digest_for_user(user_id: str, *, days: int = 1, max_per_keyword: int = 5) -> int:
    """Generate digest for a single user. Returns inbox_item id, or 0 if no content."""
    subs = subscriptions.list_for_user(user_id, kind="keyword", only_enabled=True)
    if not subs:
        return 0

    bullets: list[dict[str, Any]] = []
    for sub in subs:
        kw = sub["value"]
        papers = _fetch_papers_for_keyword(kw, days=days, max_results=max_per_keyword)
        for p in papers:
            bullets.append({"paper": p, "tldr": "", "matched_keyword": kw})

    bullets = _dedup_bullets(bullets)
    if not bullets:
        return 0

    # Generate TL;DRs (cached)
    for b in bullets:
        b["tldr"] = _tldr(b["paper"])

    title, body_md = render_digest_card(bullets)
    related = [b["paper"].get("arxiv_id") or b["paper"].get("paper_id", "") for b in bullets]
    related = [r for r in related if r]

    item_id = inbox.write(
        user_id=user_id,
        kind="daily_digest",
        title=title,
        body_md=body_md,
        actions=[
            {"label": "Ingest all", "kind": "bulk_ingest", "arg": ",".join(related)},
            {"label": "Refine subscriptions", "kind": "open_subs", "arg": ""},
        ],
        related_paper_ids=related,
    )
    try:
        from paper_rag.observability.metrics import counter
        counter("paper_rag_proactive_digest_total").inc()
    except Exception:  # noqa: BLE001 — metrics never block business logic
        pass
    log.info("daily_digest written: user=%s n_bullets=%d inbox_id=%d",
             user_id, len(bullets), item_id)
    return item_id


def _dedup_bullets(bullets: list[dict]) -> list[dict]:
    """Dedup by paper, but keep the most-relevant matched_keyword."""
    by_key: dict[str, dict] = {}
    for b in bullets:
        p = b["paper"]
        key = p.get("arxiv_id") or p.get("paper_id") or p.get("title", "")
        if key not in by_key:
            by_key[key] = b
        else:
            # Prefer first match (insertion order = sub priority by created_at)
            pass
    return list(by_key.values())


def daily_digest_for_all_users() -> dict[str, Any]:
    """Cron entry. Returns stats dict."""
    # Find all users with at least one enabled keyword sub
    user_ids: set[str] = set()
    for sub in subscriptions.iter_active():
        if sub["kind"] == "keyword":
            user_ids.add(sub["user_id"])

    n_written = 0
    n_skipped = 0
    started = time.time()
    for uid in user_ids:
        try:
            item_id = daily_digest_for_user(uid)
            if item_id:
                n_written += 1
            else:
                n_skipped += 1
        except Exception as e:
            log.exception("daily_digest_for_user(%s) failed: %s", uid, e)
            n_skipped += 1

    elapsed = time.time() - started
    log.info("daily_digest_for_all_users: users=%d written=%d skipped=%d elapsed=%.1fs",
             len(user_ids), n_written, n_skipped, elapsed)
    return {
        "n_users": len(user_ids),
        "n_written": n_written,
        "n_skipped": n_skipped,
        "elapsed_sec": round(elapsed, 1),
    }


__all__ = [
    "daily_digest_for_user",
    "daily_digest_for_all_users",
    "render_digest_card",
]
