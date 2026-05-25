"""BGE reranker (cross-encoder).

Reorders candidate chunks by query-document relevance. Lazy single instance.

Config (`reranker` block):
    enabled:    bool          turn on/off (default true)
    model_name: HuggingFace id or local path
    cache_dir:  null -> use paths.models_dir; else absolute path
    use_fp16:   bool          half-precision for speed
    top_k:      cap on returned candidates

Failure mode: if model loading fails (network / disk), we LOG a warning and
fall back to the original candidate order — never propagate the exception.
"""

from __future__ import annotations

from .. import config as cfg
from ..utils.logger import get_logger


log = get_logger("retrieve.rerank")
_MODEL = None
_LOAD_FAILED = False


def _model():
    global _MODEL, _LOAD_FAILED
    if _LOAD_FAILED:
        return None
    if _MODEL is None:
        try:
            from FlagEmbedding import FlagReranker
        except ImportError as e:
            log.warning(f"FlagEmbedding not installed: {e}; reranker disabled")
            _LOAD_FAILED = True
            return None
        c = cfg.load()
        cache_dir = c.reranker.cache_dir or c.paths.models_dir
        log.info(f"loading reranker {c.reranker.model_name} (cache={cache_dir})")
        try:
            _MODEL = FlagReranker(
                c.reranker.model_name,
                use_fp16=c.reranker.use_fp16,
                cache_dir=cache_dir,
            )
        except Exception as e:
            log.warning(f"reranker load failed: {e}; falling back to RRF order")
            _LOAD_FAILED = True
            return None
    return _MODEL


def rerank(query: str, candidates: list[dict], *, top_k: int | None = None) -> list[dict]:
    if not candidates:
        return []
    c = cfg.load()
    top_k = top_k or c.reranker.top_k
    if not c.reranker.enabled:
        return candidates[:top_k]

    model = _model()
    if model is None:
        return candidates[:top_k]

    pairs = [(query, (item.get("text") or "")) for item in candidates]
    try:
        scores = model.compute_score(pairs, normalize=True)
    except Exception as e:
        log.warning(f"reranker compute_score failed: {e}; returning RRF order")
        return candidates[:top_k]
    if isinstance(scores, float):
        scores = [scores]
    for cand, sc in zip(candidates, scores):
        cand["score_rerank"] = float(sc)
    candidates.sort(key=lambda x: x.get("score_rerank", 0.0), reverse=True)
    return candidates[:top_k]
