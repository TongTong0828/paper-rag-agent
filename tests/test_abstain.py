"""Pure-logic tests for paper_rag.rag.abstain (ADR-0014).

These tests cover the decision matrix end-to-end without touching any
external service (no Qdrant, no LLM, no FTS5). They verify:

  1. Empty chunks -> no_chunks
  2. All-low scores -> no_evidence (LLM should be skipped)
  3. Mixed scores in (low, high) -> weak_evidence
  4. All-high scores -> confident (normal flow)
  5. enabled=False -> always confident (backward compatibility)
  6. Score field absent -> falls back to confident (graceful degrade)
  7. RRF score normalization (raw 0.05 ≈ rank-1 single-list -> ~0.75)
  8. Score field priority: rerank > rrf > score
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paper_rag.rag import abstain  # noqa: E402


def _ch(score_field: str | None = "score_rerank", value: float = 0.5,
        chunk_id: str = "c1", **extra) -> dict:
    """Build a fake chunk dict the way retrieve modules do."""
    d = {"chunk_id": chunk_id}
    if score_field is not None:
        d[score_field] = value
    d.update(extra)
    return d


def test_no_chunks_returns_no_chunks():
    res = abstain.decide([])
    assert res["decision"] == abstain.DECISION_NO_CHUNKS
    assert res["n_chunks"] == 0
    assert res["evidence_score"] == 0.0


def test_all_low_scores_no_evidence():
    chunks = [
        _ch(value=0.05, chunk_id=f"c{i}") for i in range(5)
    ]
    res = abstain.decide(chunks, threshold_low=0.20, threshold_high=0.40)
    assert res["decision"] == abstain.DECISION_NO_EVIDENCE
    assert res["evidence_score"] < 0.20
    assert res["score_field"] == "score_rerank"


def test_mid_scores_weak_evidence():
    chunks = [
        _ch(value=0.30, chunk_id=f"c{i}") for i in range(5)
    ]
    res = abstain.decide(chunks, threshold_low=0.20, threshold_high=0.40)
    assert res["decision"] == abstain.DECISION_WEAK
    assert 0.20 <= res["evidence_score"] < 0.40


def test_high_scores_confident():
    chunks = [
        _ch(value=0.85, chunk_id=f"c{i}") for i in range(5)
    ]
    res = abstain.decide(chunks, threshold_low=0.20, threshold_high=0.40)
    assert res["decision"] == abstain.DECISION_CONFIDENT
    assert res["evidence_score"] >= 0.40


def test_disabled_always_confident():
    """Backward compatibility: enabled=False bypasses all thresholds."""
    chunks = [_ch(value=0.01, chunk_id=f"c{i}") for i in range(5)]
    res = abstain.decide(chunks, enabled=False, threshold_low=0.5, threshold_high=0.9)
    assert res["decision"] == abstain.DECISION_CONFIDENT
    assert res["enabled"] is False


def test_no_score_field_falls_back_confident():
    """Graceful degrade when score field is missing (e.g. legacy retrieve)."""
    chunks = [{"chunk_id": "c1", "text": "..."}, {"chunk_id": "c2", "text": "..."}]
    res = abstain.decide(chunks, threshold_low=0.5, threshold_high=0.9)
    assert res["decision"] == abstain.DECISION_CONFIDENT
    assert res["score_field"] is None


def test_rrf_score_is_normalized():
    """Raw RRF ~0.033 (rank-1 single list) should map to ~0.5 -> weak band
    under default (0.20, 0.40) is actually confident? Verify ordering."""
    chunks = [
        _ch(score_field="score_rrf", value=0.05, chunk_id=f"c{i}") for i in range(3)
    ]
    res = abstain.decide(chunks, threshold_low=0.20, threshold_high=0.40)
    # 0.05 RRF * 15 = 0.75 -> confident
    assert res["decision"] == abstain.DECISION_CONFIDENT
    assert res["score_field"] == "score_rrf"


def test_score_field_priority_rerank_over_rrf():
    """When both rerank and rrf are present, rerank wins."""
    chunks = [
        {"chunk_id": "c1", "score_rerank": 0.10, "score_rrf": 0.99},
        {"chunk_id": "c2", "score_rerank": 0.10, "score_rrf": 0.99},
        {"chunk_id": "c3", "score_rerank": 0.10, "score_rrf": 0.99},
    ]
    res = abstain.decide(chunks, threshold_low=0.20, threshold_high=0.40)
    # rerank=0.10 < 0.20 -> no_evidence (despite high rrf)
    assert res["decision"] == abstain.DECISION_NO_EVIDENCE
    assert res["score_field"] == "score_rerank"


def test_top_chunk_score_reported():
    """trace must surface top_chunk_score for debugging."""
    chunks = [
        _ch(value=0.90, chunk_id="c0"),
        _ch(value=0.10, chunk_id="c1"),
        _ch(value=0.10, chunk_id="c2"),
    ]
    res = abstain.decide(chunks, threshold_low=0.20, threshold_high=0.40)
    # mean = (0.9 + 0.1 + 0.1) / 3 = 0.367 -> weak
    assert res["decision"] == abstain.DECISION_WEAK
    assert abs(res["top_chunk_score"] - 0.90) < 1e-3
    assert abs(res["evidence_score"] - 0.367) < 1e-2


def test_min_chunks_respected():
    """min_chunks limits how many top chunks contribute to mean."""
    chunks = [_ch(value=0.90, chunk_id="c0"), _ch(value=0.90, chunk_id="c1")]
    chunks += [_ch(value=0.01, chunk_id=f"c{i}") for i in range(2, 10)]
    # Only top 2 used -> mean ~ 0.9 -> confident
    res = abstain.decide(chunks, min_chunks=2, threshold_low=0.20, threshold_high=0.40)
    assert res["decision"] == abstain.DECISION_CONFIDENT
    assert res["evidence_score"] >= 0.85


def test_low_quality_signal_fails_open():
    """When only BM25/RRF signals are available (e.g. dense down), abstain
    must NOT block — it fails open with signal_quality=low_degraded so ops
    can alert without false-positive abstentions hurting users."""
    chunks = [
        {"chunk_id": f"c{i}", "score_bm25": 1.0} for i in range(5)
    ]
    res = abstain.decide(chunks, threshold_low=0.50, threshold_high=0.80)
    # BM25=1.0 sigmoids to ~0.03; with high thresholds this would normally
    # be no_evidence — but signal_quality saves it.
    assert res["decision"] == abstain.DECISION_CONFIDENT
    assert res["signal_quality"] == "low_degraded"
    assert res["score_field"] == "score_bm25"


def test_rrf_only_treated_as_low_quality():
    """RRF alone must NOT trigger no_evidence (it's rank-based and cannot
    distinguish 'low quality match across the board')."""
    chunks = [
        {"chunk_id": f"c{i}", "score_rrf": 0.001} for i in range(5)
    ]
    res = abstain.decide(chunks, threshold_low=0.50, threshold_high=0.80)
    assert res["decision"] == abstain.DECISION_CONFIDENT
    assert res["signal_quality"] == "low_degraded"


def test_high_quality_signal_marks_high():
    """When score_dense is present, signal_quality is `high` and decision
    enforces the thresholds strictly."""
    chunks = [_ch(score_field="score_dense", value=0.10, chunk_id=f"c{i}")
              for i in range(3)]
    res = abstain.decide(chunks, threshold_low=0.30, threshold_high=0.50)
    assert res["decision"] == abstain.DECISION_NO_EVIDENCE
    assert res["signal_quality"] == "high"
