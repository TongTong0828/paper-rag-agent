"""Coverage-focused unit tests for previously-untested public surfaces.

These are NOT meant to be e2e — they exercise pure logic + module-level
monkey-patching for IO boundaries (LLM / Qdrant / SQLite).

Modules covered:
  - paper_rag.tools._schema           (pydantic validation)
  - paper_rag.tools.paper_qa          (delegation)
  - paper_rag.tools.paper_search      (group-by-paper logic)
  - paper_rag.tools.paper_compare     (NxM matrix shape)
  - paper_rag.tools.wiki_lookup       (3-tier resolution)
  - paper_rag.rag.qa_simple           (no-evidence path)
  - paper_rag.rag.history             (append/recent SQLite roundtrip)
  - paper_rag.rag.abstain._classify   (6-branch table)
  - paper_rag.wiki.normalize          (exact / alias / semantic / embed-skip)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# tools/_schema.py — pydantic validation
# ---------------------------------------------------------------------------


def test_schema_paper_search_defaults_and_limits():
    from paper_rag.tools._schema import PaperSearchInput

    s = PaperSearchInput(query="self-rag")
    assert s.top_k == 8
    assert s.year_min is None

    with pytest.raises(Exception):  # ge=1
        PaperSearchInput(query="x", top_k=0)
    with pytest.raises(Exception):  # le=30
        PaperSearchInput(query="x", top_k=31)


def test_schema_paper_compare_default_dims():
    from paper_rag.tools._schema import PaperCompareInput

    s = PaperCompareInput(paper_ids=["a", "b"])
    assert s.dimensions == ["motivation", "method", "results", "limitations"]


def test_schema_wiki_lookup_required():
    from paper_rag.tools._schema import WikiLookupInput

    with pytest.raises(Exception):
        WikiLookupInput()  # type: ignore[call-arg] — concept is required


# ---------------------------------------------------------------------------
# tools/paper_qa.py — delegation
# ---------------------------------------------------------------------------


def test_paper_qa_delegates_to_qa_agentic(monkeypatch):
    from paper_rag.tools import paper_qa as t
    from paper_rag.tools._schema import PaperQAInput

    captured = {}

    def fake_answer(q, *, paper_ids=None, **kw):
        captured["q"] = q
        captured["paper_ids"] = paper_ids
        return {"answer": "ok", "citations": [], "chunks": []}

    monkeypatch.setattr(t, "answer", fake_answer)
    out = t.paper_qa(PaperQAInput(question="What is X?", paper_ids=["a"]))
    assert out["answer"] == "ok"
    assert captured["q"] == "What is X?"
    assert captured["paper_ids"] == ["a"]


# ---------------------------------------------------------------------------
# tools/paper_search.py — best-score-per-paper grouping
# ---------------------------------------------------------------------------


def test_paper_search_groups_by_paper_and_keeps_best(monkeypatch):
    from paper_rag.tools import paper_search as t
    from paper_rag.tools._schema import PaperSearchInput

    fake_chunks = [
        {"paper_id": "p1", "title": "T1", "section": "intro", "text": "AAA", "score": 0.5},
        {"paper_id": "p1", "title": "T1", "section": "intro", "text": "BBB", "score": 0.9},
        {"paper_id": "p2", "title": "T2", "section": "method", "text": "CCC", "score": 0.4},
        {"paper_id": None, "title": None, "section": None, "text": "skip", "score": 1.0},
    ]
    monkeypatch.setattr(t, "retrieve", lambda q, top_k: fake_chunks)
    out = t.paper_search(PaperSearchInput(query="x", top_k=5))
    assert len(out) == 2
    assert out[0]["paper_id"] == "p1"
    assert out[0]["score"] == 0.9    # best chunk wins
    assert out[1]["paper_id"] == "p2"
    assert out[0]["snippet"] == "BBB"


# ---------------------------------------------------------------------------
# tools/paper_compare.py — matrix shape
# ---------------------------------------------------------------------------


def test_paper_compare_builds_matrix(monkeypatch):
    from paper_rag.tools import paper_compare as t
    from paper_rag.tools._schema import PaperCompareInput

    monkeypatch.setattr(
        t, "answer",
        lambda q, paper_ids=None: {"answer": f"{q}|{paper_ids[0]}", "citations": []},
    )
    out = t.paper_compare(PaperCompareInput(paper_ids=["p1", "p2"], dimensions=["m1", "m2"]))
    assert out["papers"] == ["p1", "p2"]
    assert out["dimensions"] == ["m1", "m2"]
    assert set(out["matrix"].keys()) == {"p1", "p2"}
    assert set(out["matrix"]["p1"].keys()) == {"m1", "m2"}
    assert "p1" in out["matrix"]["p1"]["m1"]["answer"]


# ---------------------------------------------------------------------------
# tools/wiki_lookup.py — direct hit / near-miss path
# ---------------------------------------------------------------------------


def test_wiki_lookup_direct_hit(monkeypatch):
    from paper_rag.tools import wiki_lookup as t
    from paper_rag.tools._schema import WikiLookupInput
    from paper_rag.wiki.schema import WikiEntry

    entry = WikiEntry(
        entry_id="self-rag",
        name="Self-RAG",
        category="method",
        canonical_summary="Self-reflective RAG",
        variants=[],
        aliases=[],
        cross_refs=[],
    )
    monkeypatch.setattr(t.wstore, "get_by_name", lambda n: entry)
    out = t.wiki_lookup(WikiLookupInput(concept="Self-RAG"))
    assert out["hit"] is True
    assert out["entry"]["entry_id"] == "self-rag"


def test_wiki_lookup_miss_fallback_skips_embed(monkeypatch):
    """When direct + alias both miss AND bge_m3 import fails, return []."""
    from paper_rag.tools import wiki_lookup as t
    from paper_rag.tools._schema import WikiLookupInput

    monkeypatch.setattr(t.wstore, "get_by_name", lambda n: None)
    monkeypatch.setattr(t, "find_match", lambda c: None)
    # Force the bge_m3 import-time path to fail by injecting a broken stub.
    # monkeypatch ensures cleanup at end of test (no cross-test pollution).
    import sys

    monkeypatch.setitem(sys.modules, "paper_rag.embed.bge_m3", None)
    out = t.wiki_lookup(WikiLookupInput(concept="Nonexistent Thing"))
    assert out["hit"] is False
    assert out["near_misses"] == []


# ---------------------------------------------------------------------------
# rag/qa_simple.py — empty retrieve short-circuit
# ---------------------------------------------------------------------------


def test_qa_simple_returns_no_evidence_when_retrieve_empty(monkeypatch):
    from paper_rag.rag import qa_simple

    monkeypatch.setattr(qa_simple, "retrieve", lambda q, top_k, paper_ids: [])
    out = qa_simple.answer("Anything?", top_k=5)
    assert out["answer"] == "(no evidence found)"
    assert out["citations"] == []
    assert out["chunks"] == []


# ---------------------------------------------------------------------------
# rag/history.py — append + recent roundtrip
# ---------------------------------------------------------------------------


def test_history_append_and_recent(monkeypatch, tmp_path):
    """Use a throw-away SQLite engine to verify INSERT+SELECT roundtrip."""
    from sqlalchemy import create_engine

    db_path = tmp_path / "test_history.db"
    engine = create_engine(f"sqlite:///{db_path}")

    from paper_rag.rag import history
    from paper_rag.store import sqlite_store

    monkeypatch.setattr(sqlite_store, "get_engine", lambda: engine)
    history._TABLE_READY = False  # force re-init for our throw-away engine

    history.append("conv1", "Q1?", "A1", ["chunk:a"])
    history.append("conv1", "Q2?", "A2", ["chunk:b", "chunk:c"])
    history.append("conv2", "Other", "Other", [])

    rows = history.recent("conv1", limit=5)
    assert len(rows) == 2
    assert rows[0] == ("Q1?", "A1")
    assert rows[1] == ("Q2?", "A2")


def test_history_recent_empty_for_missing_conv(monkeypatch, tmp_path):
    from sqlalchemy import create_engine

    db_path = tmp_path / "test_history2.db"
    engine = create_engine(f"sqlite:///{db_path}")

    from paper_rag.rag import history
    from paper_rag.store import sqlite_store

    monkeypatch.setattr(sqlite_store, "get_engine", lambda: engine)
    history._TABLE_READY = False
    assert history.recent("nope") == []


def test_history_rewrite_short_circuits_when_empty(monkeypatch, tmp_path):
    from sqlalchemy import create_engine

    db_path = tmp_path / "test_history3.db"
    engine = create_engine(f"sqlite:///{db_path}")

    from paper_rag.rag import history
    from paper_rag.store import sqlite_store

    monkeypatch.setattr(sqlite_store, "get_engine", lambda: engine)
    history._TABLE_READY = False

    # No history -> returns the question unchanged, never calls chat.
    monkeypatch.setattr(history, "chat", lambda *a, **kw: pytest.fail("LLM should not be called"))
    out = history.rewrite_with_history("What about it?", "missing-conv")
    assert out == "What about it?"


# ---------------------------------------------------------------------------
# rag/abstain.py — _classify table-driven
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "enabled, field, score, low, high, expected",
    [
        # disabled -> always confident
        (False, "score_rerank", 0.0, 0.2, 0.4, ("confident", "disabled")),
        # missing field -> fail open (confident)
        (True, None, 0.0, 0.2, 0.4, ("confident", "missing")),
        # low-quality field (BM25/RRF) -> fail open with degraded marker
        (True, "score_bm25", 0.05, 0.2, 0.4, ("confident", "low_degraded")),
        (True, "score_rrf", 0.05, 0.2, 0.4, ("confident", "low_degraded")),
        # standard 3-band on a high-quality field
        (True, "score_rerank", 0.10, 0.2, 0.4, ("no_evidence", "high")),
        (True, "score_rerank", 0.30, 0.2, 0.4, ("weak_evidence", "high")),
        (True, "score_rerank", 0.55, 0.2, 0.4, ("confident", "high")),
        # boundary at threshold_low
        (True, "score_rerank", 0.20, 0.2, 0.4, ("weak_evidence", "high")),
        # boundary at threshold_high
        (True, "score_rerank", 0.40, 0.2, 0.4, ("confident", "high")),
    ],
)
def test_abstain_classify_table(enabled, field, score, low, high, expected):
    from paper_rag.rag.abstain import _classify

    got = _classify(
        enabled=enabled,
        field_used=field,
        score=score,
        threshold_low=low,
        threshold_high=high,
    )
    assert got == expected


# ---------------------------------------------------------------------------
# wiki/normalize.py — direct + alias + embed-skip paths
# ---------------------------------------------------------------------------
def _stub_entry(name: str, aliases=()):
    from paper_rag.wiki.schema import WikiEntry

    return WikiEntry(
        entry_id=name.lower().replace(" ", "-"),
        name=name,
        category="method",
        canonical_summary="",
        variants=[],
        aliases=list(aliases),
        cross_refs=[],
    )


def _patch_wiki_store(monkeypatch, **stubs):
    """Monkey-patch attributes on the real paper_rag.wiki.store module so the
    lazy ``from . import store`` inside wiki.normalize sees our stubs."""
    from paper_rag.wiki import store as wstore

    for k, v in stubs.items():
        monkeypatch.setattr(wstore, k, v)


def test_normalize_direct_match(monkeypatch):
    from paper_rag.wiki import normalize

    target = _stub_entry("Self-RAG")
    _patch_wiki_store(
        monkeypatch,
        get_by_name=lambda n: target,
        list_all=lambda: [],
        search_qdrant=lambda v, top_k=3: [],
    )
    assert normalize.find_match("Self-RAG") == "self-rag"


def test_normalize_alias_match(monkeypatch):
    from paper_rag.wiki import normalize

    entries = [_stub_entry("Retrieval-Augmented Generation", aliases=["RAG"])]
    _patch_wiki_store(
        monkeypatch,
        get_by_name=lambda n: None,
        list_all=lambda: entries,
        search_qdrant=lambda v, top_k=3: [],
    )
    assert normalize.find_match("RAG") == "retrieval-augmented-generation"


def test_normalize_embed_disabled_returns_none(monkeypatch):
    from paper_rag.wiki import normalize

    _patch_wiki_store(
        monkeypatch,
        get_by_name=lambda n: None,
        list_all=lambda: [],
        search_qdrant=lambda v, top_k=3: [],
    )
    assert normalize.find_match("Anything", embed_query=False) is None


# ---------------------------------------------------------------------------
# observability/metrics.py — counter/histogram/render shapes
# ---------------------------------------------------------------------------


def test_metrics_counter_increments_and_renders():
    from paper_rag.observability import metrics

    metrics.reset()
    metrics.counter("paper_rag_test_total", labels={"k": "v"}).inc()
    metrics.counter("paper_rag_test_total", labels={"k": "v"}).inc(2)
    metrics.counter("paper_rag_test_total", labels={"k": "w"}).inc()

    snap = metrics.snapshot()
    found = {(c["labels"]["k"], c["value"]) for c in snap["counters"]}
    assert found == {("v", 3.0), ("w", 1.0)}

    text = metrics.render()
    assert "# TYPE paper_rag_test_total counter" in text
    assert 'paper_rag_test_total{k="v"} 3' in text


def test_metrics_histogram_buckets():
    from paper_rag.observability import metrics

    metrics.reset()
    h = metrics.histogram("paper_rag_test_latency_seconds")
    for v in [0.01, 0.2, 1.5, 30.0]:
        h.observe(v)

    snap = metrics.snapshot()
    hist = next(x for x in snap["histograms"] if x["name"] == "paper_rag_test_latency_seconds")
    assert hist["count"] == 4
    assert hist["sum"] == 31.71

    text = metrics.render()
    # Buckets are emitted in ascending order; +Inf must equal total count.
    assert 'paper_rag_test_latency_seconds_bucket{le="+Inf"} 4' in text
    # 0.05s bucket should hold exactly 1 sample (0.01).
    assert 'paper_rag_test_latency_seconds_bucket{le="0.05"} 1' in text


def test_metrics_histogram_time_context():
    import time

    from paper_rag.observability import metrics

    metrics.reset()
    with metrics.histogram("paper_rag_test_block_seconds").time():
        time.sleep(0.001)
    snap = metrics.snapshot()
    hist = next(x for x in snap["histograms"] if x["name"] == "paper_rag_test_block_seconds")
    assert hist["count"] == 1
    assert hist["sum"] > 0
