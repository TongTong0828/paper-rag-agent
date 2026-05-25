"""Pure-Python unit tests (no Qdrant / LLM needed)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_split_sections_basic():
    from paper_rag.chunk.section_splitter import split_sections

    md = "# Intro\nhello\n\n## Sub\nworld\n\n# Method\nbody"
    secs = split_sections(md)
    assert [s.name for s in secs] == ["Intro", "Sub", "Method"]
    assert "hello" in secs[0].body
    assert "world" in secs[1].body


def test_split_sections_no_header():
    from paper_rag.chunk.section_splitter import split_sections

    secs = split_sections("just a paragraph without headers")
    assert len(secs) == 1 and secs[0].name == "Body"


def test_multimodal_extract():
    from paper_rag.chunk.multimodal_chunker import (
        extract_figures, extract_formulas, extract_tables,
    )

    md = (
        "Some text.\n\n"
        "![alt](figures/a.png)\n\n"
        "more text\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "$$E = mc^2$$\n"
    )
    figs = extract_figures(md)
    tabs = extract_tables(md)
    forms = extract_formulas(md)
    assert len(figs) == 1 and figs[0].modality == "figure"
    assert len(tabs) == 1 and tabs[0].modality == "table"
    assert len(forms) == 1 and "E = mc^2" in forms[0].text


def test_chunk_text_token_budget():
    from paper_rag.chunk.text_chunker import chunk_text

    body = ("This is a paragraph. " * 50 + "\n\n") * 5
    chunks = chunk_text(body)
    assert len(chunks) >= 2
    for ch in chunks:
        assert ch.text


def test_citation_check_drops_invalid():
    from paper_rag.rag.citation_check import validate_citations

    retrieved = [{"chunk_id": "abc123def456"}, {"chunk_id": "0011223344"}]
    raw = "Statement one [chunk:abc123def456]. Statement two [chunk:ffffffff]."
    cleaned, valid = validate_citations(raw, retrieved)
    assert "abc123def456" in cleaned
    assert "ffffffff" not in cleaned
    assert valid == ["abc123def456"]


def test_build_chunks_smoke(tmp_path: Path):
    from paper_rag.chunk.builder import build_chunks

    md = "# Abstract\nshort abstract paragraph.\n\n# Method\nWe propose foo. We compare bar.\n\n$$y = wx + b$$\n"
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    (parsed / "paper.md").write_text(md, encoding="utf-8")
    sections, chunks = build_chunks("sha1:deadbeef", parsed, title="Sample Paper")
    assert len(sections) >= 2
    assert any(c["modality"] == "formula" for c in chunks)
    assert all(c["paper_id"] == "sha1:deadbeef" for c in chunks)
