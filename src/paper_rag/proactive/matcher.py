"""Subscription matcher (M9 / ADR-0018).

Decides whether a newly-ingested paper should trigger inbox notifications
for any user's subscriptions. Cosine similarity between
``(title + " " + abstract)`` and the subscription's `value` text, with the
threshold determined by the subscription's `strength`.

The matcher is **pure-logic + I/O**:
  - Pure: cosine math + threshold pick
  - I/O: bge-m3 encode (cached), inbox.write

Tests stub bge-m3 encoding to keep them fast.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from . import inbox, subscriptions

log = logging.getLogger(__name__)


def _cosine(a, b) -> float:
    """Numpy-free cosine similarity for compatibility with stubs."""
    if a is None or b is None:
        return 0.0
    if len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


def _encode(text: str) -> list[float] | None:
    """Encode via bge-m3; return None on failure (graceful degrade)."""
    try:
        from ..embed import bge_m3

        vec = bge_m3.encode_one(text)
        # bge_m3 returns ndarray; convert to list for portability
        return list(vec) if vec is not None else None
    except Exception as e:
        log.warning("bge-m3 encode failed for matcher: %s", e)
        return None


def match_paper_to_subs(
    paper_id: str,
    title: str,
    abstract: str,
    *,
    ingester_user_id: str | None = None,
    subs: Iterable[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return list of {subscription, similarity, threshold} for matched subs.

    Side effect: nothing (caller decides whether to write inbox).

    Parameters
    ----------
    paper_id : id used in inbox related_paper_ids
    title, abstract : text used for embedding
    ingester_user_id : skip subs owned by this user (don't notify ingester)
    subs : iterable of subscription dicts; if None, fetch all enabled
    """
    paper_text = (title or "") + "\n" + (abstract or "")
    paper_text = paper_text.strip()
    if not paper_text:
        return []
    paper_emb = _encode(paper_text)
    if paper_emb is None:
        return []

    out = []
    for sub in (subs if subs is not None else subscriptions.iter_active()):
        if ingester_user_id and sub["user_id"] == ingester_user_id:
            continue
        if sub["kind"] != "keyword":
            # v1 only handles keyword subs (topic_vector / arxiv_category come later)
            continue
        sub_emb = _encode(sub["value"])
        if sub_emb is None:
            continue
        sim = _cosine(paper_emb, sub_emb)
        threshold = subscriptions.STRENGTH_THRESHOLD.get(sub["strength"], 0.65)
        if sim >= threshold:
            out.append({
                "subscription": sub,
                "similarity": round(sim, 4),
                "threshold": threshold,
            })
    return out


def notify_matches(
    paper_id: str,
    paper_title: str,
    paper_abstract: str,
    matches: list[dict[str, Any]],
) -> int:
    """Write inbox items for each match. Returns count written."""
    written = 0
    for m in matches:
        sub = m["subscription"]
        snippet = (paper_abstract or "")[:240].rstrip()
        body_md = (
            f"### {paper_title}\n\n"
            f"{snippet}{'...' if len(paper_abstract or '') > 240 else ''}\n\n"
            f"**Why you're seeing this:** matches your subscription "
            f"`{sub['value']}` (similarity {m['similarity']:.2f} ≥ {m['threshold']:.2f})"
        )
        inbox.write(
            user_id=sub["user_id"],
            kind="sub_match",
            title=f"📚 New paper for '{sub['value']}': {paper_title[:60]}",
            body_md=body_md,
            actions=[
                {"label": "Open", "kind": "open_paper", "arg": paper_id},
                {"label": "Ask paper_qa", "kind": "qa", "arg": paper_id},
                {"label": "Mute keyword", "kind": "toggle_sub", "arg": sub["id"]},
            ],
            related_paper_ids=[paper_id],
        )
        subscriptions.mark_matched(sub["id"])
        try:
            from paper_rag.observability.metrics import counter
            counter("paper_rag_proactive_sub_match_total").inc()
        except Exception:  # noqa: BLE001
            pass
        written += 1
    return written


__all__ = ["match_paper_to_subs", "notify_matches"]
