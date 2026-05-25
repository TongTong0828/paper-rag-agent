"""Tests for the new P0 fixes (M5)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def test_suspicious_citations_numeric():
    from paper_rag.rag.citation_check import detect_suspicious_citations

    rep = detect_suspicious_citations(
        "Foo bar [1]. Other thing [chunk:abcd1234]. More [12] stuff."
    )
    assert rep["count"] == 2
    assert "[1]" in rep["numeric"]
    assert "[12]" in rep["numeric"]


def test_suspicious_citations_author_year():
    from paper_rag.rag.citation_check import detect_suspicious_citations

    rep = detect_suspicious_citations(
        "Transformers (Vaswani et al., 2017) outperform RNNs (Bahdanau 2015)."
    )
    assert rep["count"] == 2
    assert any("2017" in s for s in rep["author_year"])
    assert any("2015" in s for s in rep["author_year"])


def test_suspicious_citations_clean():
    from paper_rag.rag.citation_check import detect_suspicious_citations

    rep = detect_suspicious_citations(
        "All claims cite [chunk:abc123] and [chunk:def456]."
    )
    assert rep["count"] == 0


def test_validate_citations_drops_unknown():
    from paper_rag.rag.citation_check import validate_citations

    retrieved = [{"chunk_id": "a1b2c3d4"}, {"chunk_id": "deadbeef"}]
    raw = "S1 [chunk:a1b2c3d4]. S2 [chunk:ffffffff]. S3 [chunk:deadbeef]."
    cleaned, valid = validate_citations(raw, retrieved)
    assert "a1b2c3d4" in cleaned
    assert "deadbeef" in cleaned
    assert "ffffffff" not in cleaned
    assert set(valid) == {"a1b2c3d4", "deadbeef"}


def test_mineru_image_path_rewrite_logic():
    """The internal _IMAGE_REF_RE + rewrite should redirect to figures/."""
    from paper_rag.parse.mineru_local import _IMAGE_REF_RE

    md = "Some text\n![alt](images/fig1.png)\n"
    asset_map = {"fig1.png": "figures/fig1.png"}

    def _rewrite(m):
        alt, path = m.group(1), m.group(2)
        return f"![{alt}]({asset_map.get(Path(path).name, path)})"

    from pathlib import Path
    out = _IMAGE_REF_RE.sub(_rewrite, md)
    assert "figures/fig1.png" in out
    assert "images/fig1.png" not in out
