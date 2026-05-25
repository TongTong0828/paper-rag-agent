"""paper_search tool entry (currently dense-only over chunks, returning
distinct paper_ids ranked by best-chunk score).
"""

from __future__ import annotations

from collections import defaultdict

from ..retrieve.dense import retrieve
from ._schema import PaperSearchInput


def paper_search(input: PaperSearchInput) -> list[dict]:
    chunks = retrieve(input.query, top_k=input.top_k * 4)
    by_paper: dict[str, dict] = {}
    best: dict[str, float] = defaultdict(lambda: -1.0)
    for c in chunks:
        pid = c.get("paper_id")
        if not pid:
            continue
        score = c.get("score", 0.0)
        if score > best[pid]:
            best[pid] = score
            by_paper[pid] = {
                "paper_id": pid,
                "title": c.get("title"),
                "section": c.get("section"),
                "snippet": (c.get("text") or "")[:280],
                "score": score,
            }
    ranked = sorted(by_paper.values(), key=lambda x: x["score"], reverse=True)
    return ranked[: input.top_k]


__all__ = [
    "paper_search",
]
