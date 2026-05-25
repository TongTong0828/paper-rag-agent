"""Tests for retrieve/rag pure-logic pieces (no qdrant/llm/embed)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_rrf_basic():
    from paper_rag.retrieve.hybrid import rrf_fuse

    a = [{"chunk_id": "x"}, {"chunk_id": "y"}, {"chunk_id": "z"}]
    b = [{"chunk_id": "y"}, {"chunk_id": "w"}, {"chunk_id": "x"}]
    fused = rrf_fuse([a, b], k=60)
    ids = [d["chunk_id"] for d in fused]
    assert ids[0] in {"x", "y"}
    assert "w" in ids
    by_id = {d["chunk_id"]: d for d in fused}
    assert by_id["y"]["score_rrf"] > by_id["w"]["score_rrf"]


def test_rrf_handles_empty():
    from paper_rag.retrieve.hybrid import rrf_fuse

    assert rrf_fuse([], k=60) == []
    assert rrf_fuse([[]], k=60) == []


def test_bm25_tokenize_zh_en():
    from paper_rag.retrieve.sparse_bm25 import _tokenize

    toks = _tokenize("Hello 世界 BM25_score 你好123")
    assert "hello" in toks
    assert "世" in toks and "界" in toks
    assert "你" in toks and "好" in toks
    assert "bm25_score" in toks
    assert "123" in toks
