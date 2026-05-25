"""Reciprocal Rank Fusion + dense/sparse hybrid retrieval."""

from __future__ import annotations

from collections import defaultdict

from .. import config as cfg
from ..utils.logger import get_logger
from . import dense


log = get_logger("retrieve.hybrid")


def _sparse_search(query: str, top_k: int, paper_ids: list[str] | None) -> list[dict]:
    """Pick a sparse backend per config; fall back gracefully."""
    backend = cfg.load().retrieve.sparse_backend
    if backend == "fts5":
        try:
            from . import fts5

            return fts5.search(query, top_k=top_k, paper_ids=paper_ids)
        except Exception as e:
            log.warning(f"FTS5 backend failed, falling back to rank_bm25: {e}")
    from . import sparse_bm25

    return sparse_bm25.search(query, top_k=top_k, paper_ids=paper_ids)


def rrf_fuse(ranked_lists: list[list[dict]], k: int = 60) -> list[dict]:
    """Merge multiple ranked lists by RRF score = sum(1/(k+rank))."""
    scores: dict[str, float] = defaultdict(float)
    keep: dict[str, dict] = {}
    for lst in ranked_lists:
        for rank, item in enumerate(lst):
            cid = item.get("chunk_id")
            if not cid:
                continue
            scores[cid] += 1.0 / (k + rank + 1)
            if cid not in keep:
                keep[cid] = item
            else:
                # merge non-overlapping fields (so dense `score` survives even
                # when sparse backend doesn't have it, and vice versa)
                for k2, v in item.items():
                    keep[cid].setdefault(k2, v)
    merged = []
    for cid, sc in scores.items():
        d = dict(keep[cid])
        d["score_rrf"] = sc
        # Promote dense cosine to a stable, abstain-friendly key. RRF is
        # rank-based and does not distinguish "everything matched poorly" from
        # "everything matched well", so we keep the raw similarity as the
        # primary signal for abstain decisions (ADR-0014).
        if "score" in d and "score_dense" not in d:
            d["score_dense"] = d["score"]
        merged.append(d)
    merged.sort(key=lambda x: x["score_rrf"], reverse=True)
    return merged


def hybrid_search(query: str, *, top_k: int | None = None,
                  paper_ids: list[str] | None = None,
                  modality: str | None = None) -> list[dict]:
    c = cfg.load().retrieve
    top_k = top_k or c.rerank_top_k
    dense_hits = dense.retrieve(query, top_k=c.top_k_dense, paper_ids=paper_ids, modality=modality)
    sparse_hits = _sparse_search(query, top_k=c.top_k_bm25, paper_ids=paper_ids)
    if modality:
        sparse_hits = [h for h in sparse_hits if h.get("modality") == modality]

    fused = rrf_fuse([dense_hits, sparse_hits], k=c.rrf_k)
    log.info(
        f"hybrid: backend={c.sparse_backend} dense={len(dense_hits)} "
        f"sparse={len(sparse_hits)} fused={len(fused)}"
    )
    return fused[: top_k * 2]  # leave room for reranker
