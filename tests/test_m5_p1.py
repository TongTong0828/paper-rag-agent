"""Tests for M5 P1 features (pure-logic only)."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def test_fts5_match_query_builder():
    from paper_rag.retrieve.fts5 import _build_match_query

    assert _build_match_query("contrastive learning") == '"contrastive" OR "learning"'
    # punctuation stripped, CJK preserved
    assert _build_match_query("BM25 + 对比学习!") == '"BM25" OR "对比学习"'
    # all-empty falls back
    assert _build_match_query("???") == '""'
    # underscores allowed
    assert "param_efficient" in _build_match_query("param_efficient tuning")


def test_alias_clean_dedup_and_drop_primary():
    from paper_rag.wiki.flow import _clean_aliases

    out = _clean_aliases(
        ["Contrastive Learning", "对比学习", "CL", "", "  ", "对比学习", "x"],
        primary="Contrastive Learning",
    )
    # primary itself dropped, dup removed, single-char dropped
    assert "Contrastive Learning" not in out
    assert "对比学习" in out
    assert "CL" in out
    assert "x" not in out
    assert len(out) <= 5


def test_alias_clean_handles_non_strings():
    from paper_rag.wiki.flow import _clean_aliases

    assert _clean_aliases([None, 42, ["wrong"], {"k": "v"}, "ok"], primary="X") == ["ok"]


def test_async_queue_drains():
    """Smoke: enqueue + wait_drained returns True without errors."""
    # Patch on_paper_indexed to a no-op so we don't need DB.
    import paper_rag.wiki.queue as q
    import paper_rag.wiki.triggers as t

    called: list[str] = []

    def _stub(paper_id: str):
        called.append(paper_id)

    original = t.on_paper_indexed
    t.on_paper_indexed = _stub  # type: ignore[assignment]
    try:
        q.submit_paper_indexed("arxiv:test1")
        q.submit_paper_indexed("arxiv:test2")
        # Allow worker thread time to start + drain
        ok = q.wait_drained(timeout=5.0)
        assert ok
        assert set(called) == {"arxiv:test1", "arxiv:test2"}
    finally:
        t.on_paper_indexed = original  # type: ignore[assignment]


def test_async_queue_swallows_worker_exceptions():
    """Worker should not die on individual paper failure."""
    import paper_rag.wiki.queue as q
    import paper_rag.wiki.triggers as t

    calls: list[str] = []

    def _stub(paper_id: str):
        calls.append(paper_id)
        if paper_id == "fail":
            raise RuntimeError("boom")

    original = t.on_paper_indexed
    t.on_paper_indexed = _stub  # type: ignore[assignment]
    try:
        q.submit_paper_indexed("fail")
        q.submit_paper_indexed("ok-after-fail")
        assert q.wait_drained(timeout=5.0)
        assert "fail" in calls
        assert "ok-after-fail" in calls
    finally:
        t.on_paper_indexed = original  # type: ignore[assignment]
