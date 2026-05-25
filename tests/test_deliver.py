"""Tests for paper_rag.deliver (M10 / ADR-0016).

Pure-logic tests — we monkey-patch the underlying qa_agentic.answer and
sqlite_store.get_paper so the tests don't need Qdrant / LLM / SQLite. This
keeps them fast (<1s total) and CI-friendly.

Coverage:
1. Markdown survey: structure (headers + refs) + cite preservation
2. PPTX: file is a valid zip with ppt/ entries (.pptx is a zip)
3. DOCX: file is a valid zip with word/document.xml
4. LaTeX bib: zip contains references.bib + related_work.tex
5. Dispatch: bad format raises DeliverError
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


# ---------------------------------------------------------------------------
# Test fixtures: mock paper bundle data
# ---------------------------------------------------------------------------


def _stub_paper(paper_id="arxiv:2310.11511"):
    """Mock a SQLModel Paper row."""
    p = MagicMock()
    p.paper_id = paper_id
    p.title = "Self-RAG: Self-Reflective Retrieval-Augmented Generation"
    p.authors_json = '["Akari Asai", "Zeqiu Wu", "Yizhong Wang"]'
    p.year = 2023
    p.arxiv_id = "2310.11511"
    p.abstract = "Self-RAG enables on-demand retrieval and self-reflection..."
    p.model_dump = lambda: {
        "paper_id": p.paper_id,
        "title": p.title,
        "authors_json": p.authors_json,
        "year": p.year,
        "arxiv_id": p.arxiv_id,
        "abstract": p.abstract,
    }
    return p


def _stub_qa_answer(*args, **kwargs):
    """Stub for qa_agentic.answer that returns a deterministic summary."""
    return {
        "answer": (
            "## Motivation\n"
            "Self-RAG aims to reduce hallucination [chunk:abc12345] by "
            "selectively retrieving on-demand.\n\n"
            "## Method\n"
            "It introduces reflection tokens [chunk:def67890] that mark the "
            "need for retrieval and critique generation quality.\n\n"
            "## Results\n"
            "Outperforms baselines on diverse tasks [chunk:111aaabbb].\n\n"
            "## Limitations\n"
            "Requires a critic model and may be slow [chunk:222ccdde].\n"
        ),
        "citations": ["abc12345", "def67890", "111aaabbb", "222ccdde"],
        "trace": {"abstain": {"decision": "confident", "evidence_score": 0.71}},
        "chunks": [],
    }


def _patch_paper_rag(qa_summary=None):
    """Apply common monkey-patches and return cleanup."""
    from paper_rag.deliver import _common as common
    from paper_rag.tools import bibtex_export

    common.fetch_paper_meta = lambda pid: {
        "paper_id": pid,
        "title": "Self-RAG: Self-Reflective Retrieval-Augmented Generation",
        "authors": ["Akari Asai", "Zeqiu Wu"],
        "year": 2023,
        "arxiv_id": "2310.11511",
        "abstract": "...",
    }

    # Patch qa_agentic.answer (lazy-imported inside fetch_paper_bundle)
    import paper_rag.rag.qa_agentic as qa_mod
    qa_mod.answer = qa_summary or _stub_qa_answer

    # Patch sqlite_store for bibtex_export
    import paper_rag.store.sqlite_store as ss
    ss.get_paper = lambda pid: _stub_paper(pid)
    # bibtex_export uses Session/get_engine — make it return our stub paper
    bibtex_export.export_bibtex = lambda inp: {
        "bibtex": "\n\n".join(
            f"@misc{{{pid.replace(':', '_').replace('.', '_')},\n"
            f"  title = {{Self-RAG}},\n  year = {{2023}}\n}}"
            for pid in inp.paper_ids
        ),
        "n_exported": len(inp.paper_ids),
        "missing": [],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_markdown_survey_structure():
    """Survey contains the 3 required sections + References + at least 1 cite."""
    _patch_paper_rag()
    from paper_rag.deliver import survey_md
    # Skip the synthesis LLM call — fall back to stitched summaries
    survey_md._synthesize_outline = lambda bundles, max_words: (
        "## Introduction\n\nFake intro. [chunk:abc12345]\n\n"
        "## Methods Comparison\n\nFake methods. [chunk:def67890]\n\n"
        "## Open Problems\n\nFake open problems.\n"
    )

    res = survey_md.generate(["arxiv:2310.11511"], title="Test Survey")
    md = res.content_bytes.decode("utf-8")

    assert res.format == "markdown_survey"
    assert res.filename.endswith(".md")
    assert "# Test Survey" in md
    assert "## Introduction" in md
    assert "## Methods Comparison" in md
    assert "## Open Problems" in md
    assert "## References" in md
    assert "[chunk:abc12345]" in md  # cite preservation
    assert res.metadata["n_papers"] == 1


def test_pptx_is_valid_office_file():
    """PPTX file is a valid Office Open XML zip."""
    _patch_paper_rag()
    from paper_rag.deliver import pptx as pptx_gen

    res = pptx_gen.generate(["arxiv:2310.11511"], title="Reading Group")

    assert res.format == "pptx"
    assert res.filename.endswith(".pptx")
    # PPTX is a zip — validate structure
    z = zipfile.ZipFile(io.BytesIO(res.content_bytes))
    names = set(z.namelist())
    assert any(n.startswith("ppt/slides/slide") for n in names), \
        f"no slide xml in pptx; got {sorted(names)[:10]}"
    assert "[Content_Types].xml" in names
    assert res.metadata["n_slides"] >= 10


def test_docx_is_valid_office_file():
    """DOCX file is a valid Office Open XML zip with word/document.xml."""
    _patch_paper_rag()
    from paper_rag.deliver import survey_md
    survey_md._synthesize_outline = lambda bundles, max_words: (
        "## Introduction\n\nText. [chunk:abc12345]\n\n"
        "## Methods Comparison\n\nMore text.\n\n"
        "## Open Problems\n\nFinal.\n"
    )

    from paper_rag.deliver import docx as docx_gen
    res = docx_gen.generate(["arxiv:2310.11511"], title="Survey Doc")

    assert res.format == "docx"
    assert res.filename.endswith(".docx")
    z = zipfile.ZipFile(io.BytesIO(res.content_bytes))
    names = set(z.namelist())
    assert "word/document.xml" in names
    # Verify Heading 1 / Heading 2 styles applied (presence of `w:pStyle`)
    body = z.read("word/document.xml").decode("utf-8")
    assert "Heading" in body or "heading" in body.lower()


def test_latex_bib_zip_structure():
    """latex_bib zip has both required files."""
    _patch_paper_rag()
    from paper_rag.deliver import latex_bib

    res = latex_bib.generate(
        ["arxiv:2310.11511", "arxiv:2305.06983"],
        title="My Related Work",
        synthesize=False,  # deterministic path, no LLM
    )

    assert res.format == "latex_bib"
    assert res.filename.endswith(".zip")
    z = zipfile.ZipFile(io.BytesIO(res.content_bytes))
    names = set(z.namelist())
    assert "references.bib" in names
    assert "related_work.tex" in names
    bib = z.read("references.bib").decode("utf-8")
    tex = z.read("related_work.tex").decode("utf-8")
    assert "@misc{" in bib or "@article{" in bib
    assert "\\cite{" in tex
    assert res.metadata["n_papers"] == 2


def test_dispatch_rejects_unknown_format():
    """dispatch() raises DeliverError for unsupported format."""
    from paper_rag.deliver.dispatch import DeliverError, dispatch

    try:
        dispatch("xls", ["arxiv:2310.11511"])
    except DeliverError as e:
        assert "unsupported format" in str(e).lower()
        return
    raise AssertionError("dispatch should have raised DeliverError for 'xls'")


def test_dispatch_rejects_empty_paper_ids():
    from paper_rag.deliver.dispatch import DeliverError, dispatch

    try:
        dispatch("markdown_survey", [])
    except DeliverError as e:
        assert "non-empty" in str(e).lower()
        return
    raise AssertionError("dispatch should reject empty paper_ids")


def test_dispatch_routes_to_correct_generator():
    """dispatch() actually calls the matching submodule."""
    _patch_paper_rag()
    from paper_rag.deliver import survey_md
    survey_md._synthesize_outline = lambda bundles, max_words: (
        "## Introduction\n\nx.\n\n## Methods Comparison\n\nx.\n\n## Open Problems\n\nx.\n"
    )

    from paper_rag.deliver.dispatch import dispatch
    res = dispatch("markdown_survey", ["arxiv:2310.11511"], title="From Dispatch")
    assert res.format == "markdown_survey"
    assert "From Dispatch" in res.content_bytes.decode("utf-8")


def test_pdf_fallback_is_valid_pdf():
    """P3-15: PDF generator falls back to hand-written PDF when reportlab missing."""
    _patch_paper_rag()
    from paper_rag.deliver import survey_md
    survey_md._synthesize_outline = lambda bundles, max_words: (
        "## Introduction\n\nThe field is broad.\n\n## Methods\n\nDetails."
    )

    from paper_rag.deliver import pdf as pdf_mod

    # Force fallback by simulating reportlab failure
    original_rl = pdf_mod._reportlab_pdf_bytes
    pdf_mod._reportlab_pdf_bytes = lambda *a, **k: (_ for _ in ()).throw(ImportError("forced"))
    try:
        from paper_rag.deliver.dispatch import dispatch
        res = dispatch("pdf", ["arxiv:2310.11511"], title="PDF Test")
    finally:
        pdf_mod._reportlab_pdf_bytes = original_rl

    assert res.format == "pdf"
    assert res.content_type == "application/pdf"
    assert res.content_bytes.startswith(b"%PDF-1.4"), res.content_bytes[:20]
    assert b"%%EOF" in res.content_bytes
    assert res.metadata["pdf_engine"] == "fallback"


def main() -> int:
    tests = [
        test_markdown_survey_structure,
        test_pptx_is_valid_office_file,
        test_docx_is_valid_office_file,
        test_latex_bib_zip_structure,
        test_dispatch_rejects_unknown_format,
        test_dispatch_rejects_empty_paper_ids,
        test_dispatch_routes_to_correct_generator,
        test_pdf_fallback_is_valid_pdf,
    ]
    ok = fail = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            ok += 1
        except AssertionError as e:
            print(f"  ❌ {t.__name__}: AssertionError: {e}")
            fail += 1
        except Exception as e:
            import traceback
            print(f"  💥 {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            fail += 1
    print(f"\n{ok}/{ok+fail} passed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
