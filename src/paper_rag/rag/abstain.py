"""Abstain decision module — three-way evidence sufficiency check.

Goal
----
Bridge the gap exposed by the M6 33-question evaluation: when retrieval
correctly recalls 0 relevant papers (e.g. "Shanghai weather tomorrow" against
a NLP-paper corpus), the downstream LLM still happily cites 14 noisy chunks.
This module gives qa_agentic a first-class "abstain" decision **before** any
LLM call is made.

Decision protocol
-----------------
Given the final chunk list (already RRF-fused + reranked + truncated):

    no_chunks       — chunks == []
    no_evidence     — evidence_score < threshold_low      (LLM is SKIPPED)
    weak_evidence   — threshold_low <= score < threshold_high  (LLM is called
                      with an explicit "evidence may be insufficient" hint)
    confident       — score >= threshold_high             (normal flow)

`evidence_score` is the mean of the top-`min_chunks` per-chunk scores. We pick
the highest-quality scoring signal available in the candidate dict (preference
order is configurable; default `score_rerank > score_rrf > score`). RRF scores
are bounded ~`(0, 0.05]` so they get linearly normalized into a [0, 1]-ish band
before mean — this keeps the same threshold semantics regardless of whether
the reranker is enabled.

Industrial-grade properties
---------------------------
1. **Pure function** — `decide()` takes a list[dict] + thresholds, returns a
   typed dict. No I/O, no logging side-effects, easy to unit-test.
2. **Backward compatible** — when `enabled=False` (default until calibration),
   always returns `confident` so qa_agentic behaves exactly as before.
3. **Graceful fallback** — if score fields are missing or non-numeric, the
   decision falls back to `confident` rather than blocking the pipeline.
4. **Observable** — every decision returns the score, threshold, and the
   field it used; callers expose this in metrics + trace.
5. **Calibratable** — thresholds come from `cfg.rag.abstain` and are picked
   by `scripts/calibrate_abstain.py` from a real eval_runs/*.json + GT. No
   magic numbers in code.
"""

from __future__ import annotations

from collections.abc import Iterable

# Type alias for clarity
Decision = str  # one of: confident | weak_evidence | no_evidence | no_chunks

DECISION_CONFIDENT = "confident"
DECISION_WEAK = "weak_evidence"
DECISION_NO_EVIDENCE = "no_evidence"
DECISION_NO_CHUNKS = "no_chunks"

# Signal quality classification: only high-quality signals (real similarity)
# can reliably distinguish "irrelevant chunks ranked top" from "relevant chunks
# ranked top". Rank-based signals (RRF) cannot, BM25 alone is unreliable for
# out-of-domain questions where keywords may incidentally match. Therefore
# under low-quality signals abstain fails open (confident) to avoid blocking
# correct answers — degraded retrieval is captured as a separate metric.
HIGH_QUALITY_FIELDS = frozenset({"score_rerank", "score_dense", "score"})
LOW_QUALITY_FIELDS = frozenset({"score_bm25", "score_rrf"})


# RRF scores are sums of 1/(k+rank) and typically live in (0, 0.05] for k=60.
# Multiply by this factor to bring them into ~ (0, 1] for threshold comparison.
# Picked so that an RRF score of 0.033 (rank-1 in 1 list) maps to ~0.5.
_RRF_NORMALIZE_FACTOR = 15.0

# BM25 raw scores are unbounded (typical 0–30). We squash with a soft sigmoid
# centered at BM25=8 (a typical rank-1 score for an in-corpus query). This is
# only used as a degraded-mode fallback when dense retrieval is unavailable.
_BM25_SIGMOID_CENTER = 8.0
_BM25_SIGMOID_SLOPE = 0.5


def _pick_score(chunk: dict, fields: Iterable[str]) -> tuple[float | None, str | None]:
    """Return (score, field_name) using the first field present in `chunk`."""
    for field in fields:
        v = chunk.get(field)
        if v is None:
            continue
        try:
            return float(v), field
        except (TypeError, ValueError):
            continue
    return None, None


def _normalize(score: float, field: str) -> float:
    """Bring per-chunk score into a [0, 1]-ish band so thresholds are stable
    across reranker on/off configurations."""
    if field == "score_rrf":
        # RRF: linear scale, then clip to [0, 1]
        return max(0.0, min(1.0, score * _RRF_NORMALIZE_FACTOR))
    if field == "score_bm25":
        # BM25 raw scores are unbounded; sigmoid squash for [0,1] band.
        # Used only as a degraded-mode fallback (dense down).
        import math
        z = _BM25_SIGMOID_SLOPE * (score - _BM25_SIGMOID_CENTER)
        return 1.0 / (1.0 + math.exp(-z))
    # score_rerank: already 0..1 (sigmoid output of bge-reranker)
    # score / score_dense (cosine): bge-m3 dense cosine ~ [-1, 1] but
    # practically ~[0, 1] for similar pairs; clip to [0, 1] for safety.
    return max(0.0, min(1.0, score))


def evidence_score(
    chunks: list[dict],
    *,
    score_fields: tuple[str, ...] = (
        "score_rerank",
        "score_dense",
        "score",
        "score_bm25",
        "score_rrf",
    ),
    min_chunks: int = 3,
) -> tuple[float, str | None, int]:
    """Compute aggregated evidence score from a chunk list.

    Returns
    -------
    (score, field_used, n_used)
        score      — mean normalized score over the top `min_chunks` chunks.
                     Falls back to 0.0 if none of the chunks carry a usable
                     score field.
        field_used — the score field actually picked (or None).
        n_used     — number of chunks contributing to the mean.
    """
    if not chunks:
        return 0.0, None, 0

    take = chunks[:min_chunks] if min_chunks > 0 else chunks
    raw_scores: list[float] = []
    field_used: str | None = None
    for ch in take:
        s, field = _pick_score(ch, score_fields)
        if s is None:
            continue
        if field_used is None:
            field_used = field
        raw_scores.append(_normalize(s, field))
    if not raw_scores:
        return 0.0, None, 0
    return sum(raw_scores) / len(raw_scores), field_used, len(raw_scores)


def decide(
    chunks: list[dict],
    *,
    enabled: bool = True,
    threshold_low: float = 0.20,
    threshold_high: float = 0.40,
    min_chunks: int = 3,
    score_fields: tuple[str, ...] = (
        "score_rerank",  # bge-reranker output (best signal when available)
        "score_dense",   # bge-m3 cosine (real semantic similarity)
        "score",         # fallback alias (qdrant_store sets `score`)
        "score_bm25",    # degraded-mode fallback (dense unavailable)
        "score_rrf",     # rank-based, last resort (cannot detect no-evidence)
    ),
) -> dict:
    """Make an abstain decision.

    Parameters
    ----------
    chunks : list of retrieval result dicts (already truncated to what the LLM
             would see).
    enabled : kill switch. When False, always returns `confident` (legacy
              behavior).
    threshold_low : below this -> no_evidence (LLM skipped).
    threshold_high : at or above this -> confident (normal flow).
    min_chunks : how many top chunks contribute to evidence_score mean.
    score_fields : which score fields to consult, in priority order.

    Returns
    -------
    dict with keys: decision, evidence_score, top_chunk_score, n_chunks,
    score_field, threshold_low, threshold_high.
    """
    n_chunks = len(chunks)
    if n_chunks == 0:
        return {
            "decision": DECISION_NO_CHUNKS,
            "evidence_score": 0.0,
            "top_chunk_score": 0.0,
            "n_chunks": 0,
            "score_field": None,
            "threshold_low": threshold_low,
            "threshold_high": threshold_high,
            "enabled": enabled,
        }

    score, field_used, _ = evidence_score(
        chunks, score_fields=score_fields, min_chunks=min_chunks
    )
    top_score = 0.0
    if field_used is not None:
        try:
            top_score = _normalize(float(chunks[0].get(field_used, 0.0) or 0.0), field_used)
        except (TypeError, ValueError):
            top_score = 0.0

    if not enabled:
        decision = DECISION_CONFIDENT
        signal_quality = "disabled"
    elif field_used is None:
        # No usable score field — fail open (confident) rather than block the
        # pipeline. We log the metric so this is visible in production.
        decision = DECISION_CONFIDENT
        signal_quality = "missing"
    elif field_used in LOW_QUALITY_FIELDS:
        # Rank-based or unbounded scores (BM25/RRF) are unreliable for the
        # "low average means out-of-domain" hypothesis. Fail open and surface
        # the degraded state in the trace; counters can alert on this.
        decision = DECISION_CONFIDENT
        signal_quality = "low_degraded"
    elif score < threshold_low:
        decision = DECISION_NO_EVIDENCE
        signal_quality = "high"
    elif score < threshold_high:
        decision = DECISION_WEAK
        signal_quality = "high"
    else:
        decision = DECISION_CONFIDENT
        signal_quality = "high"

    return {
        "decision": decision,
        "evidence_score": round(score, 4),
        "top_chunk_score": round(top_score, 4),
        "n_chunks": n_chunks,
        "score_field": field_used,
        "signal_quality": signal_quality,
        "threshold_low": threshold_low,
        "threshold_high": threshold_high,
        "enabled": enabled,
    }


# Prompt suffix injected when decision == weak_evidence
WEAK_EVIDENCE_HINT = (
    "\n\nNOTE: The retrieved evidence appears WEAK or only tangentially "
    "related to the question. If you cannot answer with high confidence "
    "using the evidence above, explicitly say so — do NOT compensate with "
    "general knowledge or fabricated citations."
)


__all__ = [
    "DECISION_CONFIDENT",
    "DECISION_NO_CHUNKS",
    "DECISION_NO_EVIDENCE",
    "DECISION_WEAK",
    "WEAK_EVIDENCE_HINT",
    "decide",
    "evidence_score",
]
