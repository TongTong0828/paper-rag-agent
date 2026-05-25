"""Tests for wiki schema + consistency (pure-logic, no DB)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def test_normalize_and_make_id():
    from paper_rag.wiki.schema import make_entry_id, normalize_name

    assert normalize_name("Contrastive Learning!") == "contrastivelearning"
    assert normalize_name("FlashAttention v3") == "flashattentionv3"
    assert make_entry_id("RAG") == "concept:rag"


def test_to_row_and_back():
    """Skip when sqlmodel is unavailable; this is a DB-coupled test."""
    try:
        from paper_rag.wiki.store import _from_row, _to_row
    except ImportError:
        return
    from paper_rag.wiki.schema import Variant, WikiEntry

    e = WikiEntry(
        entry_id="concept:rag",
        name="RAG",
        aliases=["Retrieval Augmented Generation"],
        category="method",
        definition="A pattern combining retrieval with generation.",
        key_papers=["arxiv:2005.11401"],
        variants=[Variant(name="Self-RAG", summary="...", paper_id="arxiv:2310.11511")],
        related=["concept:dense_retrieval"],
        open_problems=["scaling"],
        evidence_chunks=["c1", "c2"],
    )
    row = _to_row(e)
    back = _from_row(row)
    assert back.entry_id == e.entry_id
    assert back.aliases == e.aliases
    assert back.variants[0].name == "Self-RAG"
    assert back.related == e.related
    assert back.evidence_chunks == ["c1", "c2"]


def test_consistency_flags():
    from paper_rag.wiki.consistency import check_entry, find_problematic_entries
    from paper_rag.wiki.schema import WikiEntry

    bad = WikiEntry(
        entry_id="concept:bad",
        name="Bad",
        definition="x",
        version=15,
        related=["concept:bad"],
        aliases=["a"],
    )
    issues = check_entry(bad)
    assert "definition_too_short" in issues
    assert "no_key_papers" in issues
    assert "high_version_no_evidence" in issues
    assert "self_related" in issues
    assert "trivial_alias" in issues

    good = WikiEntry(
        entry_id="concept:good",
        name="Good",
        definition="A non-trivial definition that exceeds the minimum length threshold.",
        key_papers=["arxiv:1"],
    )
    assert check_entry(good) == []
    assert find_problematic_entries([bad, good]) == [
        {"entry_id": "concept:bad", "name": "Bad", "issues": issues},
    ]
