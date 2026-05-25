"""Shared retrieve+rerank+rewrite pipeline used by both qa_agentic and qa_stream.

Keeping it here avoids the previous copy-paste of ~12 lines per call site
and ensures both code paths stay in lock-step (e.g. when we swap the rerank
model or change the candidate window from top_k*3 to something else).
"""

from __future__ import annotations

from .hybrid import hybrid_search
from .rerank import rerank as _rerank


def retrieve_round_with_rewrite(
    query: str,
    paper_ids: list[str] | None,
    top_k: int,
    *,
    rewrite_fn=None,
) -> tuple[list[dict], dict]:
    """One round of retrieval. Returns (reranked_chunks, rewrite_payload).

    ``rewrite_fn`` is injected so callers can swap in a stub during tests
    without monkey-patching the module-level rewrite. Defaults to
    ``paper_rag.rag.query_rewrite.rewrite``.
    """
    if rewrite_fn is None:
        from ..rag.query_rewrite import rewrite as rewrite_fn  # local import to avoid cycle

    rw = rewrite_fn(query)
    pooled: dict[str, dict] = {}
    for q in rw["dense_queries"]:
        for hit in hybrid_search(q, top_k=top_k, paper_ids=paper_ids):
            cid = hit.get("chunk_id")
            if not cid:
                continue
            if cid not in pooled or hit.get("score_rrf", 0) > pooled[cid].get("score_rrf", 0):
                pooled[cid] = hit
    candidates = list(pooled.values())
    candidates.sort(key=lambda x: x.get("score_rrf", 0), reverse=True)
    candidates = candidates[: top_k * 3]
    return _rerank(query, candidates, top_k=top_k), rw


def retrieve_round(query: str, paper_ids: list[str] | None, top_k: int) -> list[dict]:
    """Convenience wrapper that drops the rewrite payload."""
    chunks, _ = retrieve_round_with_rewrite(query, paper_ids, top_k)
    return chunks


__all__ = ["retrieve_round", "retrieve_round_with_rewrite"]
