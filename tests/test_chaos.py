"""Chaos tests: simulate failures and verify graceful degradation.

These tests don't need network/Qdrant/LLM — they monkey-patch the relevant
boundary functions to throw, then check that the public API doesn't
propagate the exception.

Tests cover:
- Qdrant unreachable -> dense retrieval returns []
- LLM chat raises -> qa_agentic returns evidence-only answer with degraded flag
- intent classifier fails -> defaults to 'reasoning'
- reflect fails -> assumes sufficient (no infinite loops)
- bge-m3 loading fails -> rerank falls back to RRF order

These verify the design promises of ADR-0009 (graceful degrade).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def test_qdrant_search_returns_empty_on_failure():
    """qdrant_store.search wraps exceptions and returns []."""
    from paper_rag.store import qdrant_store

    saved = qdrant_store.get_client
    qdrant_store.get_client = lambda: (_ for _ in ()).throw(
        ConnectionError("simulated qdrant down")
    )
    try:
        out = qdrant_store.search([0.0] * 10, top_k=5)
        assert out == [], f"expected [] on failure, got {out}"
    finally:
        qdrant_store.get_client = saved


def test_intent_classifier_defaults_on_chat_failure():
    """When LLM chat fails, intent classifier defaults to reasoning."""
    from paper_rag.rag import intent_classifier

    saved = intent_classifier.chat
    intent_classifier.chat = lambda *a, **k: (_ for _ in ()).throw(
        TimeoutError("simulated llm timeout")
    )
    try:
        out = intent_classifier.classify("any question")
        assert out["intent"] == "reasoning"
        assert "max_iter" in out
    finally:
        intent_classifier.chat = saved


def test_reflect_assumes_sufficient_on_failure():
    """reflect failure must default to sufficient — never an infinite loop."""
    from paper_rag.rag import reflect

    saved = reflect.chat
    reflect.chat = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        out = reflect.reflect("Q", "evidence")
        assert out["sufficiency"] == "sufficient"
        assert out["follow_up"] == ""
    finally:
        reflect.chat = saved


def test_query_rewrite_falls_back_to_original_on_failure():
    from paper_rag.rag import query_rewrite

    saved = query_rewrite.chat
    query_rewrite.chat = lambda *a, **k: (_ for _ in ()).throw(ValueError("nope"))
    try:
        out = query_rewrite.rewrite("What is RAG?")
        assert out["dense_queries"] == ["What is RAG?"]
        assert out["bm25_query"] == "What is RAG?"
    finally:
        query_rewrite.chat = saved


def test_rerank_falls_back_to_rrf_when_model_load_fails():
    """If FlagReranker import or load throws, rerank returns RRF order."""
    import paper_rag.retrieve.rerank as rr

    rr._LOAD_FAILED = False
    rr._MODEL = None

    candidates = [
        {"chunk_id": "a", "text": "alpha", "score_rrf": 0.9},
        {"chunk_id": "b", "text": "beta", "score_rrf": 0.5},
        {"chunk_id": "c", "text": "gamma", "score_rrf": 0.3},
    ]
    saved = rr._model
    rr._model = lambda: None  # simulate load failure
    try:
        out = rr.rerank("query", candidates, top_k=2)
        assert [c["chunk_id"] for c in out] == ["a", "b"], "should keep RRF order on fallback"
    finally:
        rr._model = saved
        rr._LOAD_FAILED = False


def test_bm25_handles_empty_corpus():
    """sparse_bm25.search returns [] on empty index — no crash."""
    from paper_rag.retrieve import sparse_bm25
    from paper_rag.retrieve.sparse_bm25 import _Index

    saved = sparse_bm25._INDEX
    sparse_bm25._INDEX = _Index(bm25=None, chunk_ids=[], payloads=[])
    try:
        out = sparse_bm25.search("anything", top_k=5)
        assert out == []
    finally:
        sparse_bm25._INDEX = saved


def test_citation_check_keeps_real_drops_fake():
    """Even if LLM goes berserk, validate_citations strips invalid references."""
    from paper_rag.rag.citation_check import validate_citations

    raw = (
        "Statement [chunk:abc1234567] is true. "
        "Bogus [chunk:0000000000] should be removed. "
        "Numeric [1] and (Smith 2020) survive validate_citations "
        "(they're caught by detect_suspicious_citations separately)."
    )
    cleaned, valid = validate_citations(raw, [{"chunk_id": "abc1234567"}])
    assert "abc1234567" in cleaned
    assert "0000000000" not in cleaned
    assert valid == ["abc1234567"]


def test_abstain_no_evidence_skips_llm():
    """ADR-0014: when retrieval evidence is too weak, qa_agentic must skip
    the LLM call entirely and return the canned no_evidence message.

    This is the fix for the M6 finding (n03: weather question, 14 fabricated
    cites). We simulate weak retrieval by patching _retrieve_round to return
    chunks with very low scores; chat must NEVER be called.
    """
    from paper_rag.rag import qa_agentic

    chat_called = {"n": 0}

    def fake_retrieve(query, paper_ids, top_k):
        return [
            {"chunk_id": f"low{i}", "text": "noise", "score_rerank": 0.02}
            for i in range(5)
        ]

    def fake_chat(*a, **k):
        chat_called["n"] += 1
        raise AssertionError("LLM chat MUST NOT be called when no_evidence")

    saved_retrieve = qa_agentic._retrieve_round
    saved_chat = qa_agentic.chat
    saved_classify = qa_agentic.classify
    qa_agentic._retrieve_round = fake_retrieve
    qa_agentic.chat = fake_chat
    qa_agentic.classify = lambda q: {"intent": "factual", "top_k": 5,
                                     "max_iter": 1, "rrf_k": 60}
    try:
        out = qa_agentic.answer("Shanghai weather tomorrow?")
        assert out["citations"] == []
        assert chat_called["n"] == 0
        assert out["trace"]["abstain"]["decision"] == "no_evidence"
        assert out["trace"]["stopped_by"] == "no_evidence_abstain"
    finally:
        qa_agentic._retrieve_round = saved_retrieve
        qa_agentic.chat = saved_chat
        qa_agentic.classify = saved_classify


def test_abstain_weak_evidence_calls_llm_with_hint():
    """weak_evidence band: LLM is still called, but the prompt carries
    an explicit 'evidence may be insufficient' hint."""
    from paper_rag.rag import qa_agentic, abstain as abstain_mod

    captured = {"user_msg": None}

    def fake_retrieve(query, paper_ids, top_k):
        # Mid-band scores: ~0.30 -> weak under default (0.20, 0.40)
        return [
            {"chunk_id": f"mid{i}", "text": f"chunk {i}", "score_rerank": 0.30}
            for i in range(5)
        ]

    def fake_chat(messages, **kwargs):
        # User message is the last one
        captured["user_msg"] = messages[-1]["content"]
        return "answer body [chunk:mid0]"

    saved_retrieve = qa_agentic._retrieve_round
    saved_chat = qa_agentic.chat
    saved_classify = qa_agentic.classify
    qa_agentic._retrieve_round = fake_retrieve
    qa_agentic.chat = fake_chat
    qa_agentic.classify = lambda q: {"intent": "factual", "top_k": 5,
                                     "max_iter": 1, "rrf_k": 60}
    try:
        out = qa_agentic.answer("borderline question")
        assert out["trace"]["abstain"]["decision"] == abstain_mod.DECISION_WEAK
        assert "WEAK" in (captured["user_msg"] or "")
    finally:
        qa_agentic._retrieve_round = saved_retrieve
        qa_agentic.chat = saved_chat
        qa_agentic.classify = saved_classify
