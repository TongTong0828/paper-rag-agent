"""Tests for M5 finalization features (BibTeX / metrics / streaming events / history)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


# -------- BibTeX --------

def test_bibtex_cite_key_normalization():
    from paper_rag.tools.bibtex_export import _cite_key

    assert _cite_key("arxiv:2310.11511") == "arxiv_2310_11511"
    assert _cite_key("doi:10.1109/abc.2024") == "doi_10_1109_abc_2024"
    assert _cite_key("sha1:deadbeefcafe") == "sha1_deadbeefcafe"


def test_bibtex_escape():
    from paper_rag.tools.bibtex_export import _escape_bibtex

    assert _escape_bibtex("Hello {World}") == r"Hello \{World\}"
    assert _escape_bibtex("plain") == "plain"


# -------- Metrics --------

def test_counter_inc_and_render():
    from paper_rag.observability import counter, render, reset

    reset()
    c1 = counter("test_total", {"intent": "factual"})
    c1.inc()
    c1.inc(2)
    c2 = counter("test_total", {"intent": "explore"})
    c2.inc()

    text = render()
    assert "test_total{intent=\"factual\"} 3.0" in text or "test_total{intent=\"factual\"} 3" in text
    assert "test_total{intent=\"explore\"} 1.0" in text or "test_total{intent=\"explore\"} 1" in text
    assert "# TYPE test_total counter" in text


def test_histogram_observe_buckets():
    from paper_rag.observability import histogram, render, reset

    reset()
    h = histogram("test_latency_seconds")
    for v in [0.05, 0.3, 1.5, 3.0, 7.0, 50.0]:
        h.observe(v)
    text = render()
    assert 'test_latency_seconds_bucket{le="0.5"} 2' in text  # 0.05, 0.3
    assert 'test_latency_seconds_bucket{le="5.0"} 4' in text  # +1.5, +3.0
    assert 'test_latency_seconds_count' in text
    assert 'test_latency_seconds_sum' in text


def test_histogram_quantiles():
    from paper_rag.observability import histogram, reset, snapshot

    reset()
    h = histogram("q_test")
    for v in range(1, 101):  # 1..100
        h.observe(v)
    snap = snapshot()
    his = snap["histograms"][0]
    assert his["count"] == 100
    assert 49 <= his["p50"] <= 51
    assert 94 <= his["p95"] <= 96


# -------- Trace id --------

def test_trace_id_unique_and_short():
    from paper_rag.observability import new_trace_id

    ids = {new_trace_id() for _ in range(50)}
    assert len(ids) == 50
    for i in ids:
        assert len(i) == 16
        assert all(c in "0123456789abcdef" for c in i)


# -------- Streaming events --------

def test_stream_event_shape_when_no_evidence():
    """Smoke test: when retrieval is empty, stream emits done with degraded reason."""
    from paper_rag.rag import qa_stream

    # Force empty retrieval (manual patch — no pytest fixtures)
    saved = (qa_stream._retrieve_round, qa_stream.classify, qa_stream.rewrite)
    qa_stream._retrieve_round = lambda q, p, k: ([], {"dense_queries": [q], "bm25_query": q})
    qa_stream.classify = lambda q: {"intent": "factual", "top_k": 5, "max_iter": 1, "rrf_k": 60}
    qa_stream.rewrite = lambda q: {"dense_queries": [q], "bm25_query": q, "raw": {}}
    try:
        events = list(qa_stream.stream_answer("Q", paper_ids=None))
        types = [e["event"] for e in events]
        assert "intent" in types
        assert types[-1] == "done"
        assert events[-1]["data"].get("degraded") == "no_chunks"
    finally:
        qa_stream._retrieve_round, qa_stream.classify, qa_stream.rewrite = saved
